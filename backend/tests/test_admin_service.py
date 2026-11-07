"""admin_service: права ручек, редактор, DOI, очередь, bulk, люди, кабинет автора."""

import asyncio
import io
import os
import tempfile
import uuid
import zipfile

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import UploadFile
from fastapi.routing import APIRoute
from sqlalchemy import any_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

import shared.uow as uow_module
from admin_service.src.api import exports_router, my_router, people_router, router
from admin_service.src.dto import PersonIn
from admin_service.src.main import app as admin_app
from admin_service.src.service import doi as crossref
from admin_service.src.service.author_service import AuthorService
from admin_service.src.service.export_service import ExportService
from admin_service.src.service.people_service import PeopleService
from admin_service.src.service.works_service import WorkService
from catalog_service.src.service.catalog_service import CatalogService
from shared.auth.models import User
from shared.auth.session import SessionUser, require_author, require_moderator
from shared.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    UpstreamError,
)
from shared.imports import ImportItem
from shared.people import Person
from shared.refdata import Department
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_ro_factory, uow_write_factory
from shared.works import Material, Work, WorkPerson
from tests.fakes import FakeStorage, anonymous_everywhere
from tests.fakes import deps as _deps


def catalog() -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), FakeStorage())  # как в api.py


def works() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage())


def page(**params: str) -> dict:
    """Фильтры фасетов как из строки запроса."""
    return {k: [v] for k, v in params.items()}


async def test_review_queue_and_unsure_ai(demo: dict) -> None:
    s = works()
    listed = await s.list(sort="problems", page=1, page_size=200, status="draft")
    assert any(i.startswith("ИИ не уверен") for w in listed["items"] for i in w["issues"])
    first = (await s.next_in_queue(None, None, None))["id"]
    assert first and (await s.next_in_queue(uuid.UUID(first), None, None))["id"] != first
    summary = await s.summary()
    assert summary["drafts"]["total"] > 0 and summary["pendingMaterials"][0]["name"] == "Авторская версия статьи.pdf"


async def test_editor_marks_manual_and_doi_conflicts(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    s = works()
    tpl = s.template("publication")
    untitled = await s.create({"type": "publication"}, demo["user"])  # тело без title: раньше 500
    assert untitled["status"] == "draft" and not untitled["title"]
    created = await s.create(tpl | {"title": "Моё название"}, demo["user"])
    assert list(created["fieldMeta"]) == ["title"]
    assert created["errors"] and created["actions"] == ["publish", "reject"]

    async def lookup(doi: str) -> dict:
        return {
            "title": "Графовые нейронные сети",
            "authors": ["Орлов Д. С.", "Миронов К. П."],
            "venue": "Компьютерные исследования",
            "year": 2026,
            "issn": "2076-7633",
            "kind": "journal_article",
        }

    monkeypatch.setattr(crossref, "lookup", lookup)
    work_id = uuid.UUID(created["id"])
    res = await s.fill_from_doi(work_id, "https://doi.org/10.12345/KIM.2026.0142", [], demo["user"])
    assert [c["field"] for c in res["conflicts"]] == ["title"]
    assert res["work"]["fieldMeta"]["venue"] == {"source": "doi"} and res["work"]["doi"] == "10.12345/kim.2026.0142"
    assert res["work"]["authors"][0]["personId"] == str(demo["ids"]["p-s1"])  # автор сопоставлен
    res = await s.fill_from_doi(work_id, "10.12345/kim.2026.0142", ["title"], demo["user"])
    assert res["conflicts"] == [] and res["work"]["fieldMeta"]["title"] == {"source": "doi"}
    published = await s.publish(work_id, demo["user"])
    assert published["status"] == "published" and published["journalLists"]
    with pytest.raises(ConflictError):
        await s.publish(work_id, demo["user"])
    assert (await catalog().work(work_id))["view"]["external"]["label"] == "Открыть по DOI"


async def test_related_links_are_symmetric(demo: dict) -> None:
    s = works()
    talk_id, pub_id = demo["ids"]["talk-1"], demo["ids"]["pub-1"]
    other_pub = demo["ids"]["pub-2"]
    await s.patch(talk_id, {"relatedPublicationId": str(other_pub)}, demo["user"])
    assert (await s.get(other_pub))["relatedTalkId"] == str(talk_id)
    assert (await s.get(pub_id))["relatedTalkId"] is None  # старая пара разорвана
    await s.patch(talk_id, {"relatedPublicationId": str(pub_id)}, demo["user"])


async def test_bulk_checks_consent_and_db_enforces_it(demo: dict) -> None:
    s = works()
    no_consent = next(
        w for w in demo["data"]["works"] if w["type"] == "vkr" and not w["consent"] and w["status"] == "published"
    )
    res = await s.bulk([demo["ids"][no_consent["id"]]], "access", "full", demo["user"])
    assert res == {"done": 0, "skipped": [no_consent["title"]]}
    with pytest.raises(DBAPIError, match="consent"):
        async with uow_write_factory() as uow:
            await uow.session.execute(
                update(Work).where(Work.id == demo["ids"][no_consent["id"]]).values(access_level="full")
            )


async def test_people_match_and_merge(demo: dict) -> None:
    p = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    unmatched = await p.unmatched(demo["user"])
    belova = next(u for u in unmatched["items"] if u["displayName"] == "Белова Н.")
    assert belova["suggestions"][0]["fullName"] == "Белова Наталья Викторовна"
    await p.match("Белова Н.", uuid.UUID(belova["suggestions"][0]["id"]), "staff", demo["user"])
    assert "Белова Н." not in {u["displayName"] for u in (await p.unmatched(demo["user"]))["items"]}
    async with uow_write_factory() as uow:
        items = (
            await uow.session.execute(select(ImportItem).where(any_(ImportItem.unmatched_authors) == "Белова Н."))
        ).all()
    assert items == []
    # то же написание инициалами вперёд в старой работе: точное совпадение привязывается само, без кнопки
    async with uow_write_factory() as uow:
        work_id = (await uow.session.execute(select(Work.id).limit(1))).scalar_one()
        uow.session.add(WorkPerson(work_id=work_id, role="author", position=99, display_name="Н. Белова", kind="staff"))
    listed = await p.unmatched(demo["user"])
    assert "Н. Белова" not in {u["displayName"] for u in listed["items"]}
    assert all(w["title"] for u in listed["items"] for w in u["works"])  # не голые id работ
    async with uow_write_factory() as uow:
        linked = await uow.session.get(WorkPerson, (work_id, "author", 99))
    assert linked.person_id == uuid.UUID(belova["suggestions"][0]["id"])
    dup = await p.create(PersonIn(fullName="Орлов Д.С.", kind="staff"))
    main = demo["ids"]["p-s1"]
    card = await p.merge(main, uuid.UUID(dup["id"]), demo["user"])
    assert "Орлов Д.С." in card["aliases"]
    assert (await catalog().profile(uuid.UUID(dup["id"])))["id"] == str(main)  # старый id отдаёт основной профиль


async def test_rest_become_external_authors(demo: dict) -> None:
    p = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    before = (await p.unmatched(demo["user"]))["items"]
    rest = [u["displayName"] for u in before if not u["suggestions"]]
    assert rest
    # «Нет такого» никто не видел в списке — его нет и в работах: пропускается, а не заводится
    assert await p.match_external([*rest, rest[0], "Нет Такого А."], demo["user"]) == {"done": len(rest)}
    after = (await p.unmatched(demo["user"]))["items"]
    assert {u["displayName"] for u in after} == {u["displayName"] for u in before if u["suggestions"]}
    async with uow_write_factory() as uow:
        kinds = set(await uow.session.scalars(select(Person.kind).where(Person.full_name.in_(rest))))
    assert kinds == {"external"}


async def test_writer_role_reads_materials(demo: dict) -> None:
    """admin_service ходит в базу ролью writer: из users ей выдана только часть колонок."""
    async with uow_write_factory() as uow:
        wp = (
            await uow.session.execute(
                select(WorkPerson)
                .join(Work)
                .where(Work.type == "publication", WorkPerson.role == "author", WorkPerson.person_id.is_not(None))
                .limit(1)
            )
        ).scalar_one()
        author = User(id=uuid.uuid7(), login="author1", password_hash="x", role="author", name="Автор А. А.")
        author.person_id = wp.person_id
        uow.session.add(author)
        await uow.session.flush()
        uow.session.add(
            Material(work_id=wp.work_id, name="слайды.pdf", size=1, kind="slides", file_key="k", uploaded_by=author.id)
        )
    writer = create_async_engine(demo["url"], connect_args={"server_settings": {"role": "writer"}})
    uow_module.sessions.configure(bind=writer)
    try:
        user = SessionUser(author.id, author.name, "author", wp.person_id, True, 0)
        mine = await AuthorService(uow_write_factory, FakeStorage()).works(user)
        assert "Автор А. А." in [m["uploadedBy"] for i in mine["items"] for m in i["materials"]]
        assert "Автор А. А." in [m["uploadedBy"] for m in (await works().get(wp.work_id))["materials"]]
        mat = next(m for i in mine["items"] for m in i["materials"] if m["uploadedBy"] == "Автор А. А.")
        await works().review_material(wp.work_id, uuid.UUID(mat["id"]), "rejected", demo["user"], " Не тот файл ")
        mine = await AuthorService(uow_write_factory, FakeStorage()).works(user)
        assert [m["rejectReason"] for i in mine["items"] for m in i["materials"] if m["id"] == mat["id"]] == [
            "Не тот файл"
        ]
    finally:
        uow_module.sessions.configure(bind=create_async_engine(demo["url"]))
        await writer.dispose()


async def test_xlsx_closes_temp_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    """mkstemp отдаёт открытый fd: без os.close каждая выгрузка оставляла бы его открытым."""
    fds = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, name = real(*args, **kwargs)
        fds.append(fd)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)

    async def rows():
        yield ["a"]

    path, count = await ExportService(uow_write_factory, FakeStorage())._xlsx(rows(), ["h"])
    path.unlink()
    assert count == 1
    with pytest.raises(OSError):
        os.fstat(fds[0])


@pytest.mark.parametrize("kind", ["csv", "zip"])
async def test_aborted_export_is_audited(demo: dict, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Клиент оборвал загрузку после первого куска — персональные данные уже ушли, выгрузка в журнале."""
    audited = []
    monkeypatch.setattr(ExportService, "_audit", lambda self, user, what, *a, **kw: audited.append(what))
    s = ExportService(uow_write_factory, FakeStorage())
    filters = dict.fromkeys(("type_", "year", "status", "import_id", "department", "q"))
    res = await (s.works_table(filters, "csv", demo["user"]) if kind == "csv" else s.works_zip(filters, demo["user"]))
    body = res.body_iterator
    assert await anext(body)
    await body.aclose()
    assert audited == [f"works.{kind}"]


async def test_author_deletes_only_own_material(demo: dict) -> None:
    async with uow_write_factory() as uow:
        wp = (
            await uow.session.execute(
                select(WorkPerson)
                .join(Work)
                .where(Work.type == "talk", WorkPerson.role == "author", WorkPerson.person_id.is_not(None))
                .limit(1)
            )
        ).scalar_one()
        # два входа на одного человека — как соавторы одной работы: работа обоим своя
        users = [User(id=uuid.uuid7(), login=f"co{i}-{uuid.uuid4()}", password_hash="x", role="author") for i in (1, 2)]
        for i, u in enumerate(users):
            u.name, u.person_id = f"Соавтор {i}", wp.person_id
            uow.session.add(u)
        await uow.session.flush()
        m = Material(work_id=wp.work_id, name="m.pdf", size=1, kind="text", file_key="k", uploaded_by=users[0].id)
        uow.session.add(m)
    owner, coauthor = (SessionUser(u.id, u.name, "author", wp.person_id, True, 0) for u in users)
    s = AuthorService(uow_write_factory, FakeStorage())
    with pytest.raises(ForbiddenError):
        await s.delete(wp.work_id, m.id, coauthor)
    await s.delete(wp.work_id, m.id, owner)
    async with uow_ro_factory() as uow:
        assert await uow.session.get(Material, m.id) is None


async def test_merge_keeps_chains_flat_and_locks(demo: dict) -> None:
    p = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    a, b, c = [uuid.UUID((await p.create(PersonIn(fullName=f"Слияние {x}", kind="external")))["id"]) for x in "АБВ"]
    await p.merge(b, a, demo["user"])
    await p.merge(c, b, demo["user"])
    async with uow_ro_factory() as uow:
        assert (await uow.session.get(Person, a)).merged_into == c  # не a → b → c

    x, y = [uuid.UUID((await p.create(PersonIn(fullName=f"Встречно {n}", kind="external")))["id"]) for n in "ГД"]
    results = await asyncio.gather(p.merge(x, y, demo["user"]), p.merge(y, x, demo["user"]), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1, results
    assert all(isinstance(r, (dict, BadRequestError, ConflictError)) for r in results), results
    async with uow_ro_factory() as uow:
        merged = [(await uow.session.get(Person, i)).merged_into for i in (x, y)]
    assert merged.count(None) == 1  # без блокировки оба смотрели бы друг на друга


def pdf(name: str = "a.pdf") -> UploadFile:
    body = b"%PDF-1.4 test"
    return UploadFile(file=io.BytesIO(body), filename=name, size=len(body))


async def test_published_work_stays_complete_and_stale_form_is_refused(demo: dict) -> None:
    s = works()
    published = next(w for w in demo["data"]["works"] if w["status"] == "published" and w["type"] == "publication")
    work_id = demo["ids"][published["id"]]
    current = await s.get(work_id)
    with pytest.raises(UnprocessableError, match="оставаться полной"):
        await s.patch(work_id, {"title": ""}, demo["user"])
    assert (await s.get(work_id))["title"] == published["title"]
    saved = await s.patch(work_id, current | {"annotation": "Правка первого"}, demo["user"])
    with pytest.raises(ConflictError, match="уже изменили"):  # второй модератор открыл форму до этой правки
        await s.patch(work_id, current | {"annotation": "Правка второго"}, demo["user"])
    await s.patch(work_id, saved | {"annotation": "Правка второго"}, demo["user"])


async def test_bulk_access_on_incomplete_drafts_and_republish(demo: dict) -> None:
    s = works()
    draft = await s.create({"type": "publication", "title": "Черновик без авторов"}, demo["user"])
    assert draft["errors"]
    res = await s.bulk([uuid.UUID(draft["id"])], "access", "hidden", demo["user"])
    assert res == {"done": 1, "skipped": []}
    published = next(w for w in demo["data"]["works"] if w["status"] == "published")
    assert await s.bulk([demo["ids"][published["id"]]], "publish", None, demo["user"]) == {"done": 0, "skipped": []}


async def test_unmatched_links_exact_names_once_under_concurrency(demo: dict) -> None:
    p = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    person = await p.create(PersonIn(fullName="Гусева Ольга Петровна", kind="staff"))
    async with uow_write_factory() as uow:
        work_id = (await uow.session.execute(select(Work.id).limit(1))).scalar_one()
        # то же написание без пробелов: точное совпадение по ключу, и оно добавится к человеку новым вариантом
        uow.session.add(
            WorkPerson(work_id=work_id, role="author", position=98, display_name="Гусева О.П.", kind="staff")
        )
    await p.patch(uuid.UUID(person["id"]), PersonIn(aliases=["Гусева О. П."]))
    first, second = await asyncio.gather(p.unmatched(demo["user"]), p.unmatched(demo["user"]))  # без блокировки — 500
    assert "Гусева О.П." not in {u["displayName"] for u in first["items"] + second["items"]}
    async with uow_ro_factory() as uow:
        assert (await uow.session.get(WorkPerson, (work_id, "author", 98))).person_id == uuid.UUID(person["id"])


async def test_replaced_work_file_is_deleted_but_shared_demo_file_is_not(demo: dict) -> None:
    storage = FakeStorage()
    s = WorkService(uow_write_factory, WorkRepository(), PersonRepository(), storage)
    demo_work = next(w for w in demo["data"]["works"] if w.get("hasFile"))
    work_id = demo["ids"][demo_work["id"]]
    storage.objects["demo/sample.pdf"] = b"%PDF-1.4 demo"
    await s.upload_file(work_id, pdf())
    assert "demo/sample.pdf" in storage.objects  # общий образец демо-работ
    first = next(k for k in storage.objects if k.startswith(f"works/{work_id}/"))
    await s.upload_file(work_id, pdf("b.pdf"))
    assert first not in storage.objects and len([k for k in storage.objects if k.startswith("works/")]) == 1


async def test_zip_skips_missing_objects(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    class Missing(FakeStorage):
        async def stream(self, key: str):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            yield b""

    audited = []
    monkeypatch.setattr(ExportService, "_audit", lambda self, user, what, filters, rows, files=0: audited.append(files))
    filters = dict.fromkeys(("type_", "year", "status", "import_id", "department", "q"))
    res = await ExportService(uow_write_factory, Missing()).works_zip(filters, demo["user"])
    body = b"".join([chunk async for chunk in res.body_iterator])
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert zf.testzip() is None and zf.namelist() == ["works.xlsx"]
    assert audited == [0]


async def test_people_and_author_small_fixes(demo: dict) -> None:
    p = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    past_end = await p.list(q=None, kind=None, department=None, exclude=None, page=10_000, page_size=50)
    assert past_end["items"] == [] and past_end["total"] > 0
    renamed = await p.create(PersonIn(fullName="Смирнова Анна Сергеевна", kind="staff"))
    card = await p.patch(uuid.UUID(renamed["id"]), PersonIn(fullName="Кузнецова Анна Сергеевна", aliases=[]))
    assert card["aliases"] == ["Смирнова Анна Сергеевна"]  # старые работы под прежней фамилией найдут её

    s = works()
    author = demo["ids"]["p-s1"]
    ref = {"personId": str(author), "displayName": "Орлов Д. С.", "kind": "staff"}
    rejected = await s.create({"type": "talk", "title": "Отклонённый доклад", "authors": [ref]}, demo["user"])
    await s.reject(uuid.UUID(rejected["id"]), "дубль", demo["user"])
    user = SessionUser(demo["user"].id, "Орлов Д. С.", "author", author, True, 0)
    with pytest.raises(NotFoundError):  # в «Моих работах» её нет — загрузить к ней тоже нельзя
        await AuthorService(uow_write_factory, FakeStorage()).upload(uuid.UUID(rejected["id"]), pdf(), "text", user)

    for title in ("Доклад раз", "Доклад два"):
        await s.create({"type": "talk", "title": title, "event": {"name": "Одна конференция"}}, demo["user"])
    names = [e["name"] for e in await s.events("Одна конфер")]
    assert names == ["Одна конференция"]


async def test_crossref_html_instead_of_json(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="<html>maintenance</html>"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    with pytest.raises(UpstreamError):
        await crossref.lookup("10.1234/x")


ADMIN_ROUTERS = [
    (router, require_moderator),
    (people_router, require_moderator),
    (exports_router, require_moderator),
    (my_router, require_author),
]


@pytest.mark.parametrize(("router", "guard"), ADMIN_ROUTERS)
def test_routes_need_role(router, guard) -> None:
    """Роутер без зависимости открыл бы /api/admin/* или /api/my/* всем."""
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert routes
    for r in routes:
        assert guard in _deps(r.dependant), r.path


async def test_anonymous_gets_401_everywhere() -> None:
    await anonymous_everywhere(admin_app, [r for r, _ in ADMIN_ROUTERS])


async def test_admin_search_prefix_and_typos(demo: dict) -> None:
    s = works()
    exact = await s.list(sort=None, page=1, page_size=200, q="нейронные")
    assert exact["total"] > 0
    for q in ("нейр", "нейроные", "нецронных"):
        res = await s.list(sort=None, page=1, page_size=200, q=q)
        assert any("нейрон" in w["title"].lower() for w in res["items"]), q
    drafts = await s.list(sort=None, page=1, page_size=200, q="нейр", status="draft")
    assert all(w["status"] == "draft" for w in drafts["items"])  # черновики модератор находит тем же поиском


async def test_department_only_from_reference(demo: dict) -> None:
    """Кафедра — справочник: «123» из формы человека или редактора работы не заводит новую кафедру."""
    people = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    with pytest.raises(BadRequestError, match="нет в справочнике"):
        await people.create(PersonIn(fullName="Новиков Пётр Ильич", kind="staff", department="123"))
    s = works()
    created = await s.create(s.template("publication") | {"title": "Кафедра только из списка"}, demo["user"])
    with pytest.raises(BadRequestError, match="нет в справочнике"):
        await s.patch(uuid.UUID(created["id"]), {"department": "123"}, demo["user"])
    saved = await s.patch(uuid.UUID(created["id"]), {"department": "Кафедра информационных систем"}, demo["user"])
    assert saved["department"] == "Кафедра информационных систем"
    async with uow_ro_factory() as uow:
        assert await uow.session.get(Department, "123") is None


# ---------- ORCID, Crossref и цитирования (проверка на работах И. В. Оселедца, ORCID 0000-0003-2071-2163) ----------


def test_crossref_mapping_orcid_article_number_language() -> None:
    got = crossref.from_crossref(
        {
            "title": ["Scalable Cross-Entropy Loss"],
            "type": "journal-article",
            "article-number": "165116",
            "is-referenced-by-count": 744,
            "author": [
                {"given": "Ivan", "family": "Oseledets", "ORCID": "https://orcid.org/0000-0003-2071-2163"},
                {"name": "Moscow Polytechnic University, Moscow, Russia"},  # организация, не автор
                {"given": "Gleb", "family": "Mezentsev"},
                {"given": "Irina O.", "family": "CHEREPANOVA"},
            ],
        }
    )
    assert got["authors"] == ["Oseledets I.", "Mezentsev G.", "Cherepanova I. O."]
    assert got["author_orcids"] == ["0000-0003-2071-2163", None, None]
    assert got["pages"] == "165116"  # у Physical Review вместо страниц — номер статьи
    assert got["language"] == "en"  # Crossref язык не дал: по названию, а не шаблонное «ru»
    assert got["citations"] == 744
    assert crossref.from_crossref({"title": ["Ускоренный алгоритм"]})["language"] == "ru"
    both = crossref.from_crossref(
        {
            "ISSN": ["2470-0029", "2470-0010"],
            "issn-type": [{"type": "electronic", "value": "2470-0029"}, {"type": "print", "value": "2470-0010"}],
        }
    )
    assert (both["issn"], both["issns"]) == ("2470-0010", ["2470-0010", "2470-0029"])  # печатный первым


def test_orcid_checksum() -> None:
    from admin_service.src.service.people_service import orcid_checksum_ok

    assert orcid_checksum_ok("0000-0003-2071-2163") and orcid_checksum_ok("0000-0002-1825-0097")
    assert orcid_checksum_ok("0000-0002-1694-233X")
    assert not orcid_checksum_ok("0000-0003-2071-2164")


async def test_citation_sources_batch_and_parse() -> None:
    from admin_service.src import citations

    dois = [f"10.1/{i}" for i in range(51)] + ["10.1002/(sici)1,2"]
    assert [len(b) for b in citations.batches(dois)] == [50, 1, 1]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            items = [{"DOI": "10.1/A", "is-referenced-by-count": 7}]
            return httpx.Response(200, json={"message": {"items": items}})
        return httpx.Response(200, json={"results": [{"doi": "https://doi.org/10.1/a", "cited_by_count": 9}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        assert await citations.crossref(http, ["10.1/a"]) == {"10.1/a": 7}
        assert await citations.openalex(http, ["10.1/a"]) == {"10.1/a": 9}


async def test_doi_links_author_by_orcid_and_h_index(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from admin_service.src import citations

    s = works()
    people = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    person = await people.create(
        PersonIn(fullName="Оселедец Иван Валерьевич", kind="staff", orcid="0000-0003-2071-2163")
    )
    with pytest.raises(BadRequestError, match="контрольный"):
        await people.create(PersonIn(fullName="Опечатка", kind="staff", orcid="0000-0003-2071-2164"))
    counts = {"10.9999/a": 801, "10.9999/b": 456, "10.9999/c": 22, "10.9999/d": 3}

    async def lookup(doi: str) -> dict:
        orcid = "0000-0003-2071-2163" if doi.endswith("a") else None  # ORCID в Crossref есть не у всех статей
        return {
            "title": f"Tensor work {doi}",
            "authors": ["Khrulkov V.", "Oseledets I."],
            "author_orcids": [None, orcid],
            "venue": "SIAM Journal",
            "year": 2018,
            "kind": "journal_article",
            "citations": counts[doi] - 1,
        }

    monkeypatch.setattr(crossref, "lookup", lookup)
    ids = []
    for doi in counts:  # первая — с ORCID: написание «Oseledets I.» запоминается, остальные сопоставятся по нему
        created = await s.create(s.template("publication"), demo["user"])
        res = await s.fill_from_doi(uuid.UUID(created["id"]), doi, [], demo["user"])
        assert res["work"]["authors"][1]["personId"] == person["id"], doi
        assert res["work"]["citations"] == counts[doi] - 1  # Crossref сразу при заполнении
        await s.publish(uuid.UUID(created["id"]), demo["user"])
        ids.append(created["id"])
    assert "Oseledets I." in (await people.card(uuid.UUID(person["id"])))["aliases"]

    async def openalex(http: httpx.AsyncClient, dois: list[str]) -> dict[str, int]:
        return {d: counts[d] for d in dois if d in counts}

    async def crossref_down(http: httpx.AsyncClient, dois: list[str]) -> dict[str, int]:
        raise httpx.ConnectError("нет сети")

    stats = await citations.update(None, {"crossref": crossref_down, "openalex": openalex})
    assert stats["errors"] >= 1 and stats["rows"] >= len(counts)
    metrics = await catalog().metrics(uuid.UUID(person["id"]), None)
    h, total = (int(t["value"]) for t in metrics["items"])
    assert (h, total) == (3, 801 + 456 + 22 + 3)  # максимум по источникам: OpenAlex на единицу больше Crossref


async def test_orcid_works_parsed_self_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def ext(value: str, rel: str = "self", kind: str = "doi") -> dict:
        return {"external-id-type": kind, "external-id-value": value, "external-id-relationship": rel}

    body = {
        "group": [
            {"external-ids": {"external-id": [ext("https://doi.org/10.1137/090752286"), ext("1234", kind="eid")]}},
            {"external-ids": {"external-id": [ext("10.1137/090752286")]}},  # та же работа из другого источника
            {"external-ids": {"external-id": [ext("10.1007/978-3-030", rel="part-of"), ext("10.1007/X_5")]}},
            {"external-ids": None},
        ]
    }

    async def get(source: str, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        assert url == "https://pub.orcid.org/v3.0/0000-0003-2071-2163/works" and headers == {
            "Accept": "application/json"
        }
        return body

    monkeypatch.setattr(crossref, "_get", get)
    assert await crossref.orcid_dois("0000-0003-2071-2163") == ["10.1137/090752286", "10.1007/x_5"]


async def test_import_from_orcid(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    s = works()
    people = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    nobody = await people.create(PersonIn(fullName="Безорсидов Пётр Ильич", kind="staff"), demo["user"])
    with pytest.raises(BadRequestError, match="ORCID"):
        await s.import_orcid(uuid.UUID(nobody["id"]), demo["user"])

    person = await people.create(
        PersonIn(fullName="Рахуба Максим Владимирович", kind="staff", orcid="0000-0002-1825-0097"), demo["user"]
    )
    # однофамилица с теми же инициалами: по транслиту «Rakhuba M. V.» не угадать, а владелец профиля известен
    namesake = await people.create(PersonIn(fullName="Рахуба Мария Викторовна", kind="staff"), demo["user"])
    known = await s.create(s.template("publication") | {"title": "Уже в базе", "doi": "10.7777/known"}, demo["user"])

    async def orcid_dois(orcid: str) -> list[str]:
        assert orcid == "0000-0002-1825-0097"
        return ["10.7777/a", "10.7777/known", "10.7777/gone", "10.7777/dup"]

    async def lookup_many(dois: list[str]) -> dict[str, dict]:
        assert "10.7777/known" not in dois  # известные DOI в Crossref не спрашиваем
        card = {"authors": ["Rakhuba M. V.", "Smith J."], "venue": "J. Comp. Phys.", "year": 2016}
        out = {"10.7777/a": card | {"title": "Tensor trains", "kind": "journal_article", "citations": 5}}
        out["10.7777/dup"] = card | {"title": "Уже в базе", "year": known["year"]}
        out["10.7777/oa"] = card | {"title": "From OpenAlex"}
        return {d: out[d] for d in dois if d in out}

    async def openalex_dois(orcid: str) -> list[str]:
        return ["10.7777/a", "10.7777/oa"]  # OpenAlex знает статью, которой нет в профиле

    monkeypatch.setattr(crossref, "orcid_dois", orcid_dois)
    monkeypatch.setattr(crossref, "openalex_dois", openalex_dois)
    monkeypatch.setattr(crossref, "lookup_many", lookup_many)
    res = await s.import_orcid(uuid.UUID(person["id"]), demo["user"])
    assert res == {
        "found": 5,
        "created": 3,
        "known": 1,
        "notFound": ["10.7777/gone"],
        "failed": [],
        "message": "DOI в профиле ORCID: 4, ещё 1 нашёл OpenAlex. Новых черновиков: 3, уже в базе: 1, нет в Crossref: 1.",
    }

    card = await people.card(uuid.UUID(person["id"]))
    titles = {w["title"]: w for w in card["works"]}  # «Rakhuba M. V.» — владелец профиля, хоть однофамилица и есть
    assert set(titles) == {"Tensor trains", "Уже в базе", "From OpenAlex"}
    assert titles["Tensor trains"]["status"] == "draft"
    assert (await people.card(uuid.UUID(namesake["id"])))["works"] == []
    work = await s.get(uuid.UUID(titles["Tensor trains"]["id"]))
    assert work["doi"] == "10.7777/a" and work["fieldMeta"]["title"]["source"] == "doi" and work["citations"] == 5
    dup = titles["Уже в базе"]
    assert (await s.get(uuid.UUID(dup["id"])))["duplicateOf"] == known["id"]

    again = await s.import_orcid(uuid.UUID(person["id"]), demo["user"])
    assert (again["created"], again["known"]) == (0, 4)

    # пачка DOI текстом: ссылки doi.org, запятые, известные пропускаются
    with pytest.raises(BadRequestError, match="ни одного DOI"):
        await s.import_doi_list("тут нет DOI", demo["user"])
    batch = await s.import_doi_list("https://doi.org/10.7777/A, 10.7777/known;\n10.7777/gone.", demo["user"])
    assert (batch["found"], batch["created"], batch["known"], batch["notFound"]) == (3, 0, 2, ["10.7777/gone"])
    assert batch["message"] == "DOI в списке: 3. Новых черновиков: 0, уже в базе: 2, нет в Crossref: 1."


def test_isbn_checksum_and_open_library(monkeypatch: pytest.MonkeyPatch) -> None:
    assert crossref.normalize_isbn("ISBN 978-5-02-013850-6") == "9785020138506"
    assert crossref.normalize_isbn("5-02-013850-9") == "5020138509"
    assert crossref.normalize_isbn("0-8044-2957-X") == "080442957X"
    with pytest.raises(BadRequestError, match="контрольным"):
        crossref.normalize_isbn("978-5-02-013850-9")


async def test_isbn_lookup_falls_back_to_open_library(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def get(source: str, url: str, params: dict | None = None, headers: dict | None = None, *, follow=False):
        calls.append((source, url.rsplit("/", 1)[-1], follow))
        if source == "Crossref":
            return {"message": {"items": []}}
        if url.endswith(".json") and "isbn" in url:  # издание
            return {
                "title": "Тензорные поезда",
                "subtitle": "теория и практика",
                "publishers": ["Наука", "Другое"],
                "publish_date": "March 2020",
                "number_of_pages": 312,
                "languages": [{"key": "/languages/rus"}],
            }
        return {"docs": [{"author_name": ["Иванов, Иван Петрович", "Ivan Oseledets"]}]}

    monkeypatch.setattr(crossref, "_get", get)
    got = await crossref.lookup_isbn("9785020138506")
    assert calls == [
        ("Crossref", "works", False),
        ("Open Library", "9785020138506.json", True),
        ("Open Library", "search.json", False),
    ]
    assert got == {
        "title": "Тензорные поезда: теория и практика",
        "authors": ["Иванов И. П.", "Oseledets I."],
        "publisher": "Наука",
        "year": 2020,
        "pages": "312",
        "kind": "monograph",
        "language": "ru",
    }


async def test_fill_from_isbn(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    s = works()

    async def lookup_isbn(isbn: str) -> dict:
        assert isbn == "9785020138506"
        return {
            "title": "Книга по ISBN",
            "authors": ["Орлов Д. С."],
            "publisher": "Наука",
            "year": 2021,
            "kind": "monograph",
        }

    monkeypatch.setattr(crossref, "lookup_isbn", lookup_isbn)
    created = await s.create(s.template("publication"), demo["user"])
    res = await s.fill_from_isbn(uuid.UUID(created["id"]), "978-5-02-013850-6", [], demo["user"])
    w = res["work"]
    assert (w["title"], w["publisher"], w["kind"], w["isbn"]) == (
        "Книга по ISBN",
        "Наука",
        "monograph",
        "9785020138506",
    )
    assert w["fieldMeta"]["publisher"] == {"source": "isbn"} and w["authors"][0]["personId"]


def test_initials_fit() -> None:
    from shared.repository.people_repository import initials_fit, is_latin

    assert initials_fit("Oseledets I.", "Оселедец Иван Валерьевич")
    assert initials_fit("I. V. Oseledets", "Оселедец Иван Валерьевич")
    assert not initials_fit("Oseledets I. A.", "Оселедец Иван Валерьевич")
    assert is_latin("Oseledets I.") and not is_latin("Оселедец И.") and not is_latin("Oseledets И.")


async def test_latin_spelling_matches_russian_name_and_relinks_at_once(demo: dict) -> None:
    s = works()
    people = PeopleService(uow_write_factory, PersonRepository(), WorkRepository())
    # статья пришла раньше, чем человека завели: автор нераспознан
    early = await s.create(
        {
            "type": "publication",
            "title": "Ранняя статья",
            "authors": [{"displayName": "Khrulkova V. S.", "kind": "external"}],
        },
        demo["user"],
    )
    assert early["authors"][0]["personId"] is None
    v = await people.create(PersonIn(fullName="Хрулькова Валентина Сергеевна", kind="staff"), demo["user"])
    assert (await s.get(uuid.UUID(early["id"])))["authors"][0]["personId"] == v["id"]  # сразу, без «Это он»
    assert "Khrulkova V. S." in (await people.card(uuid.UUID(v["id"])))["aliases"]

    async with uow_write_factory() as uow:
        repo = PersonRepository()
        got = await repo.match_many(uow, ["Khrulkova V.", "V. S. Khrulkova", "Khrulkova A."])
    assert {n: p.full_name for n, p in got.items()} == {
        "Khrulkova V.": "Хрулькова Валентина Сергеевна",
        "V. S. Khrulkova": "Хрулькова Валентина Сергеевна",
    }
    # однофамилица с той же первой буквой: «Khrulkova V.» уже не угадываем, а по отчеству различаем
    await people.create(PersonIn(fullName="Хрулькова Вера Петровна", kind="staff"), demo["user"])
    async with uow_write_factory() as uow:
        got = await PersonRepository().match_many(uow, ["Khrulkova V.", "Khrulkova V. P."])
    assert {n: p.full_name for n, p in got.items()} == {"Khrulkova V. P.": "Хрулькова Вера Петровна"}


async def test_seed_people_from_staff_csv(demo: dict, tmp_path) -> None:
    """Таблица кафедры: новый человек с новой кафедрой и ORCID, существующий — обновляется; повтор не дублирует."""
    from admin_service.src import seed

    path = tmp_path / "staff.csv"
    path.write_text(
        "ФИО;Кафедра;Должность;Степень;Звание;ORCID\n"
        "Ноздрин Вадим Станиславович;Факультет информационных технологий;Доцент;Кандидат наук;;0000-0001-6114-0435\n"
        "Орлов Дмитрий Сергеевич;;Профессор;;;\n",
        encoding="utf-8",
    )
    await seed.load_people(path)  # раньше: MissingGreenlet на новом человеке
    await seed.load_people(path)
    async with uow_ro_factory() as uow:
        rows = (
            await uow.session.execute(select(Person).where(Person.full_name == "Ноздрин Вадим Станиславович"))
        ).scalars()
        rows = list(rows)
        orlov = await uow.session.get(Person, demo["ids"]["p-s1"])
    assert len(rows) == 1 and rows[0].orcid == "0000-0001-6114-0435" and "Ноздрин В. С." in rows[0].aliases
    assert rows[0].department == "Факультет информационных технологий" and orlov.position == "Профессор"


async def test_nightly_sync_updates_untouched_doi_fields(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from admin_service.src import sync

    s = works()
    card = {
        "title": "Sync article",
        "authors": ["Орлов Д. С."],
        "venue": "Journal",
        "year": 2026,
        "kind": "journal_article",
    }

    async def lookup(doi: str) -> dict:
        return dict(card, volume="1")

    monkeypatch.setattr(crossref, "lookup", lookup)
    created = await s.create(s.template("publication"), demo["user"])
    work_id = uuid.UUID(created["id"])
    await s.fill_from_doi(work_id, "10.8888/sync", [], demo["user"])
    before = await s.get(work_id)
    await s.patch(work_id, {"volume": "2", "updatedAt": before["updatedAt"]}, demo["user"])  # том поправил модератор

    async def lookup_many(dois: list[str]) -> dict[str, dict]:
        return {"10.8888/sync": dict(card, volume="9", issue="4", pages="10–20")} if "10.8888/sync" in dois else {}

    monkeypatch.setattr(crossref, "lookup_many", lookup_many)
    stats = await sync.refresh_dois(sync.service())
    assert stats["updated"] == 1 and stats["errors"] == 0
    w = await s.get(work_id)
    assert (w["volume"], w["issue"], w["pages"]) == ("2", "4", "10–20")  # ручная правка цела, новое — из Crossref
    assert w["fieldMeta"]["volume"]["source"] == "manual" and w["fieldMeta"]["issue"]["source"] == "doi"
    stamp = w["updatedAt"]
    assert (await sync.refresh_dois(sync.service()))["updated"] == 0  # второй проход ничего не меняет
    assert (await s.get(work_id))["updatedAt"] == stamp


async def test_openalex_pages_without_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = []

    async def get(source: str, url: str, params: dict | None = None, headers: dict | None = None, *, follow=False):
        pages.append(params["page"])
        if params["page"] == 3:
            raise UpstreamError("OpenAlex ответил ошибкой")  # третья страница не пришла — собранное остаётся
        return {
            "results": [{"doi": f"https://doi.org/10.1/P{params['page']}-{i}"} for i in range(200)] + [{"doi": None}]
        }

    monkeypatch.setattr(crossref, "_get", get)
    got = await crossref.openalex_dois("0000-0003-2071-2163")
    assert pages == [1, 2, 3] and len(got) == 400 and got[0] == "10.1/p1-0"


async def test_rate_limited_source_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"message": {"DOI": "x"}})]
    sent, waits = [], []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(crossref.asyncio, "sleep", no_wait)

    async def fake_request(self, method, url, params=None):
        sent.append(url)
        return answers[len(sent) - 1]

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    assert await crossref._get("Crossref", "https://api.crossref.org/works/x") == {"message": {"DOI": "x"}}
    assert len(sent) == 2
    assert 2 <= waits[0] <= 2.6  # Retry-After плюс джиттер до 30 %
