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
