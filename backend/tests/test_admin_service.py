"""admin_service: права ручек, редактор, DOI, очередь, bulk, люди, кабинет автора."""

import uuid

import pytest
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
from admin_service.src.service.people_service import PeopleService
from admin_service.src.service.works_service import WorkService
from catalog_service.src.service.catalog_service import CatalogService
from shared.auth.models import User
from shared.auth.session import SessionUser, require_author, require_moderator
from shared.exceptions import BadRequestError, ConflictError
from shared.imports import ImportItem
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
    res = await s.fill_from_doi(work_id, "https://doi.org/10.12345/KIM.2026.0142", [])
    assert [c["field"] for c in res["conflicts"]] == ["title"]
    assert res["work"]["fieldMeta"]["venue"] == {"source": "doi"} and res["work"]["doi"] == "10.12345/kim.2026.0142"
    assert res["work"]["authors"][0]["personId"] == str(demo["ids"]["p-s1"])  # автор сопоставлен
    res = await s.fill_from_doi(work_id, "10.12345/kim.2026.0142", ["title"])
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
    await s.patch(talk_id, {"relatedPublicationId": str(other_pub)})
    assert (await s.get(other_pub))["relatedTalkId"] == str(talk_id)
    assert (await s.get(pub_id))["relatedTalkId"] is None  # старая пара разорвана
    await s.patch(talk_id, {"relatedPublicationId": str(pub_id)})


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
    unmatched = await p.unmatched()
    belova = next(u for u in unmatched["items"] if u["displayName"] == "Белова Н.")
    assert belova["suggestions"][0]["fullName"] == "Белова Наталья Викторовна"
    await p.match("Белова Н.", uuid.UUID(belova["suggestions"][0]["id"]), "staff")
    assert "Белова Н." not in {u["displayName"] for u in (await p.unmatched())["items"]}
    async with uow_write_factory() as uow:
        items = (
            await uow.session.execute(select(ImportItem).where(any_(ImportItem.unmatched_authors) == "Белова Н."))
        ).all()
    assert items == []
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
    finally:
        uow_module.sessions.configure(bind=create_async_engine(demo["url"]))
        await writer.dispose()


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
        await s.patch(uuid.UUID(created["id"]), {"department": "123"})
    saved = await s.patch(uuid.UUID(created["id"]), {"department": "Кафедра информационных систем"})
    assert saved["department"] == "Кафедра информационных систем"
    async with uow_ro_factory() as uow:
        assert await uow.session.get(Department, "123") is None
