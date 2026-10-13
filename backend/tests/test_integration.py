"""С настоящей PostgreSQL (testcontainers): миграция, справочники, демо-сид и сценарии из src/mocks/logic.test.ts."""

import asyncio
import io
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import URL, any_, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from admin_service import doi as crossref
from admin_service import refdata, seed
from admin_service.my import AuthorService
from admin_service.people import PeopleService, PersonIn
from admin_service.service import WorkService
from catalog_service.service import CatalogService
from import_service.service import ImportService
from shared.auth import SessionUser
from shared.bus.consumer import Delivery
from shared.contracts import ItemExtractedDTO
from shared.db import uow as uow_module
from shared.db.uow import uow_ro_factory, uow_write_factory
from shared.exceptions import ConflictError, NotFoundError
from shared.models import Import, ImportItem, Material, Person, User, Work, WorkPerson
from shared.repository.people import PersonRepository
from shared.repository.works import WorkRepository
from tests.fakes import FakePublisher, FakeStorage

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="нужен Docker для testcontainers")
DELIVERY = Delivery(redelivered=False, delivery_count=0, retries=0)


@pytest.fixture(scope="session")
async def demo() -> dict:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17", username="app", password="app", dbname="app", driver=None) as pg:
        host, port = pg.get_container_host_ip(), int(pg.get_exposed_port(5432))
        env = os.environ | {"DB_HOST": host, "DB_PORT": str(port), "DB_USER": "app", "DB_PASSWORD": "app"}
        alembic = [sys.executable, "-m", "alembic", "upgrade", "head"]
        await asyncio.to_thread(subprocess.run, alembic, cwd=ROOT, env=env, check=True)
        engine = create_async_engine(URL.create("postgresql+asyncpg", "app", "app", host, port, "app"))
        uow_module.sessions.configure(bind=engine)
        await refdata.init()
        data, ids = await seed.write_demo()
        moderator = User(id=uuid.uuid7(), login="moderator", password_hash="x", role="moderator", name="Модератор")
        async with uow_write_factory() as uow:
            uow.session.add(moderator)
        yield {
            "data": data,
            "ids": ids,
            "user": SessionUser(moderator.id, "Модератор", "moderator", None, True, 0),
            "url": URL.create("postgresql+asyncpg", "app", "app", host, port, "app"),
        }
        await engine.dispose()


def catalog() -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), FakeStorage())  # как в api.py


def works() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage())


def page(**params: str) -> dict:
    """Фильтры фасетов как из строки запроса."""
    return {k: [v] for k, v in params.items()}


async def test_catalog_shows_only_published_visible(demo: dict) -> None:
    c = catalog()
    scopus = await c.catalog(
        page(indexing="scopus"), q=None, type_="publication", exclude=None, sort=None, page=1, page_size=100
    )
    assert scopus["total"] > 0
    assert all(w["type"] == "publication" and "scopus" in w["indexing"] for w in scopus["items"])
    total = (await c.catalog({}, q=None, type_=None, exclude=None, sort=None, page=1, page_size=1))["total"]
    facets = await c.facets({}, q=None, type_=None)
    assert sum(f["count"] for f in facets["type"]) == total
    seen = set()
    for p in range(1, 6):
        res = await c.catalog({}, q=None, type_=None, exclude=None, sort=None, page=p, page_size=100)
        seen |= {w["id"] for w in res["items"]}
    assert len(seen) == total
    ids = demo["ids"]
    assert str(ids["vkr-14"]) not in seen  # скрыта
    assert str(ids["cw-7"]) not in seen  # отклонена
    with pytest.raises(NotFoundError):
        await c.work(ids["cw-7"])


async def test_drafts_are_invisible_publicly(demo: dict) -> None:
    c = catalog()
    drafts = [w for w in demo["data"]["works"] if w["status"] == "draft"]
    for w in drafts[:5]:
        work_id = demo["ids"][w["id"]]
        for call in (c.work, c.file, c.citation):
            with pytest.raises(NotFoundError):
                await call(work_id)


async def test_search_morphology_and_facets(demo: dict) -> None:
    c = catalog()
    res = await c.catalog({}, q="нейронные сети", type_=None, exclude=None, sort=None, page=1, page_size=20)
    assert res["total"] > 0
    lists = await c.catalog(
        page(journalList="white1"), q=None, type_="publication", exclude=None, sort=None, page=1, page_size=100
    )
    assert lists["total"] > 0
    assert all("Белый список, уровень 1" in w["journalLists"] for w in lists["items"])
    facets = await c.facets(page(year="1999"), q=None, type_="publication")
    assert {"value": "1999", "label": "1999", "count": 0} in facets["year"]  # выбранное без совпадений остаётся
    assert facets["grnti"] == []  # счётчики соседних фасетов — по выдаче с выбранным годом
    facets = await c.facets({}, q=None, type_="publication")
    assert any(f["label"].startswith("28.23 ") for f in facets["grnti"])


async def test_home_and_people(demo: dict) -> None:
    c = catalog()
    assert len({w["type"] for w in await c.latest()}) == 4
    orlov = demo["ids"]["p-s1"]
    staff = await c.staff(q="Орлов Д. С.", departments=[], sort=None)
    assert [p["id"] for p in staff["items"]] == [str(orlov)]
    metrics = await c.metrics(orlov, None)
    h, cites = (int(t["value"]) for t in metrics["items"])
    assert 0 < h <= cites
    one = await c.metrics(orlov, str(metrics["years"][0]))
    assert int(one["items"][1]["value"]) <= cites
    graduate = next(p for p in demo["data"]["people"] if p["kind"] == "graduate")
    profile = await c.profile(demo["ids"][graduate["id"]])
    assert profile["tabs"][0]["query"] == {"type": "vkr", "role": "author"} and "Выпускник" in profile["headline"]
    talk = (await c.catalog({}, q=None, type_="talk", exclude=None, sort=None, page=1, page_size=1))["items"][0]
    assert talk["view"]["kind"].startswith("Доклад (") and talk["view"]["byline"][0]["label"] == "Докладчики:"
    assert (await c.citation(uuid.UUID(talk["id"])))["gost"]


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


async def test_table_import(demo: dict) -> None:
    rows = (
        "Авторы;Название;Вид;Журнал/сборник;Год;DOI\n"
        '"Орлов Д. С.; Неизвестный А. А.";Новая статья о графах;Статья в журнале;Журнал;2026;\n'
        "Орлов Д. С.;Без года;Тезисы;Сборник;;\n"
        "Орлов Д. С.;Ссылка;Тезисы;Сборник;20x6;\n"
    )
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), FakePublisher())
    file = UploadFile(io.BytesIO(rows.encode()), filename="pubs.csv", size=len(rows.encode()))
    imp = await service.create("table", "publication", file, None, demo["user"])
    assert imp["status"] == "done"
    detail = await service.get(uuid.UUID(imp["id"]), None)
    statuses = [(i["ref"], i["status"], i.get("error")) for i in detail["items"]]
    assert statuses == [
        ("Строка 2", "filled", None),
        ("Строка 3", "error", "Не указан год"),
        ("Строка 4", "error", "Неверное значение: Год"),
    ]
    assert detail["items"][0]["unmatchedAuthors"] == ["Неизвестный А. А."]
    assert detail["import"]["stats"] == {
        "total": 3,
        "processed": 3,
        "filled": 1,
        "duplicates": 0,
        "errors": 2,
        "unmatched": 1,
    }


async def test_archive_item_from_worker(demo: dict) -> None:
    storage = FakeStorage()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), storage, FakePublisher())
    imp = Import(
        id=uuid.uuid7(), kind="archive", work_type="vkr", file_name="a.zip", status="processing", created_by="Модератор"
    )
    items = [
        ImportItem(id=uuid.uuid7(), import_id=imp.id, n=n, ref=f"{n}.pdf", file_key=f"imports/{imp.id}/items/{n}.pdf")
        for n in (1, 2)
    ]
    for it in items:
        storage.objects[it.file_key] = b"%PDF-1.4"
    async with uow_write_factory() as uow:
        uow.session.add(imp)
        await uow.session.flush()
        uow.session.add_all(items)
    fields = {
        "title": "Новая ВКР о графах",
        "authors": ["Петров Пётр Петрович"],
        "supervisors": ["Белова Наталья Викторовна"],
        "department": "Кафедра информационных систем",
        "directionCode": "09.03.02",
        "year": 2026,
        "level": "bachelor",
    }
    meta = {k: {"source": "ai", "confidence": 0.85} for k in fields}
    await service.on_extracted(ItemExtractedDTO(item_id=items[0].id, fields=fields, field_meta=meta), DELIVERY)
    await service.on_extracted(ItemExtractedDTO(item_id=items[0].id, error="повтор"), DELIVERY)  # идемпотентно
    detail = await service.get(imp.id, None)
    assert detail["import"]["status"] == "processing" and detail["items"][0]["status"] == "filled"
    await service.on_extracted(ItemExtractedDTO(item_id=items[1].id, error="Скан без текстового слоя"), DELIVERY)
    detail = await service.get(imp.id, None)
    assert detail["import"]["status"] == "done" and detail["items"][1]["error"] == "Скан без текстового слоя"
    work = await works().get(uuid.UUID(detail["items"][0]["workId"]))
    assert work["fieldMeta"]["supervisors"]["confidence"] == 0.95  # подтверждено по базе
    assert work["direction"] == {"code": "09.03.02", "name": "Информационные системы и технологии"}
    assert work["hasFile"] and work["issues"] == ["Не распознан автор: Петров Пётр Петрович"]


async def test_db_roles_have_only_their_rights(demo: dict) -> None:
    async with uow_write_factory() as uow:
        await uow.session.execute(text("SET LOCAL ROLE writer"))
        await uow.session.execute(text("SELECT id, role, token_version FROM users"))
        with pytest.raises(DBAPIError, match="permission denied"):
            async with uow.session.begin_nested():
                await uow.session.execute(text("SELECT password_hash FROM users"))
    for role, sql in (
        ("catalog", "SELECT id FROM users"),
        ("catalog", "UPDATE works SET title = title"),
        ("auth", "SELECT id FROM works"),
        ("grafana_ro", "SELECT id FROM works"),
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with uow_write_factory() as uow:
                await uow.session.execute(text(f"SET LOCAL ROLE {role}"))
                await uow.session.execute(text(sql))
    async with uow_write_factory() as uow:
        await uow.session.execute(text("SET LOCAL ROLE grafana_ro"))
        assert (await uow.session.execute(text("SELECT sum(works) FROM stats.works_total"))).scalar() > 0
    async with uow_write_factory() as uow:
        assert await uow.session.get(Person, demo["ids"]["p-s1"])


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
