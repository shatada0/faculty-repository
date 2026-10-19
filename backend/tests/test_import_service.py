"""import_service: таблицы, zip, импорт таблицы и результат воркера по архиву."""

import io
import uuid
import zipfile

import pytest
from fastapi import UploadFile
from fastapi.routing import APIRoute

from admin_service.src.service.works_service import WorkService
from import_service.src.api import router as import_router
from import_service.src.main import app as import_app
from import_service.src.service import archive, records
from import_service.src.service.import_service import ImportService
from shared.auth.session import require_moderator
from shared.bus.consumer import Delivery
from shared.bus.contracts import ItemExtractedDTO
from shared.exceptions import BadRequestError
from shared.imports import Import, ImportItem
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_write_factory
from tests.fakes import FakePublisher, FakeStorage, anonymous_everywhere
from tests.fakes import deps as _deps


def _zip(files: dict[str, bytes], symlink: str | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    return buf


DELIVERY = Delivery(redelivered=False, delivery_count=0, retries=0)


def works() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage())


def test_table_rows_to_drafts() -> None:
    fields, meta, authors = records.publication(
        {
            "Название": "Paper",
            "Авторы": "Иванов И. И.; Петров П. П.",
            "Год": "2024",
            "Вид": "Статья в журнале",
            "Журнал/сборник": "J",
            "ISSN/ISBN": "ISBN 978-5-1",
            "Индексация": "Scopus, ВАК",
        }
    )
    assert authors == ["Иванов И. И.", "Петров П. П."]
    assert fields["language"] == "en" and fields["kind"] == "journal_article" and fields["isbn"] == "978-5-1"
    assert fields["indexing"] == ["scopus", "vak"] and fields["issn"] is None
    assert meta["title"] == {"source": "table"} and "volume" not in meta
    fields, _, presenters, _ = records.talk(
        {
            "Название доклада": "Доклад",
            "Докладчики": "А",
            "Конференция": "К",
            "Уровень": "международный",
            "Дата начала": "12.05.2024",
        }
    )
    assert presenters == ["А"]
    assert (
        fields["event"]["dateStart"] == "2024-05-12"
        and fields["year"] == 2024
        and fields["event"]["level"] == "international"
    )
    assert records.row_error(["Год", "Название"]) == "Не указан год"


def test_zip_only_pdfs_no_paths_no_links(tmp_path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(
        _zip({"../../evil/Иванов_ВКР.pdf": b"%PDF-1", "notes.txt": b"x", "inner.zip": b"PK"}, "link.pdf").getvalue()
    )
    out = archive.extract_pdfs(path, tmp_path)
    assert [name for name, _ in out] == ["Иванов_ВКР.pdf"]
    assert out[0][1] == tmp_path / "1.pdf"  # имя из архива в путь не попало


def test_zip_limits(tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_zip({f"{i}.pdf": b"%PDF-1" for i in range(3)}).getvalue())
    monkeypatch.setattr(archive, "MAX_FILES", 2)
    with pytest.raises(archive.ArchiveError):
        archive.extract_pdfs(path, tmp_path)
    monkeypatch.setattr(archive, "MAX_FILES", 10)
    monkeypatch.setattr(archive, "MAX_TOTAL", 10)
    with pytest.raises(archive.ArchiveError, match="2 ГБ"):
        archive.extract_pdfs(path, tmp_path)


async def test_table_import(demo: dict) -> None:
    rows = (
        "Авторы;Название;Вид;Журнал/сборник;Год;DOI\n"
        '"Орлов Д. С.; Неизвестный А. А.";Новая статья о графах;Статья в журнале;Журнал;2026;\n'
        "Орлов Д. С.;Без года;Тезисы;Сборник;;\n"
        "Орлов Д. С.;Ссылка;Тезисы;Сборник;20x6;\n"
    )
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), FakePublisher())
    file = UploadFile(io.BytesIO(rows.encode()), filename="pubs.csv", size=len(rows.encode()))
    imp = await service.create("table", "publication", [file], None, demo["user"])
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
        "published": 0,
        "duplicates": 0,
        "errors": 2,
        "unmatched": 1,
    }


async def test_archive_item_from_worker(demo: dict) -> None:
    storage, publisher = FakeStorage(), FakePublisher()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), storage, publisher)
    imp = Import(
        id=uuid.uuid7(), kind="archive", work_type="vkr", file_name="a.zip", status="processing", created_by="Модератор"
    )
    items = [
        ImportItem(id=uuid.uuid7(), import_id=imp.id, n=n, ref=f"{n}.pdf", file_key=f"imports/{imp.id}/items/{n}.pdf")
        for n in (1, 2, 3)
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
    clean = ItemExtractedDTO(item_id=items[0].id, fields=fields | {"workType": "vkr"}, field_meta=meta)
    await service.on_extracted(clean, DELIVERY)
    await service.on_extracted(ItemExtractedDTO(item_id=items[0].id, error="повтор"), DELIVERY)  # идемпотентно
    detail = await service.get(imp.id, None)
    assert detail["import"]["status"] == "processing" and detail["items"][0]["status"] == "published"
    await service.on_extracted(ItemExtractedDTO(item_id=items[1].id, error="Скан без текстового слоя"), DELIVERY)
    # курсовой проект в том же импорте; ФИО автора модель не взяла из текста — профиль не заводим, карточка ждёт модератора
    guessed = meta | {"authors": {"source": "ai", "confidence": 0.4}}
    project = fields | {"title": "Курсовой проект о графах", "authors": ["Сидоров С. С."]}
    project |= {"workType": "coursework", "courseworkKind": "project"}
    await service.on_extracted(ItemExtractedDTO(item_id=items[2].id, fields=project, field_meta=guessed), DELIVERY)
    detail = await service.get(imp.id, None)
    assert detail["import"]["status"] == "done" and detail["items"][1]["error"] == "Скан без текстового слоя"
    assert detail["import"]["stats"]["published"] == 1 and detail["import"]["stats"]["filled"] == 1

    work = await works().get(uuid.UUID(detail["items"][0]["workId"]))
    assert work["status"] == "published" and work["accessLevel"] == "card_only" and work["issues"] == []
    assert work["fieldMeta"]["supervisors"]["confidence"] == 0.95  # подтверждено по базе
    assert work["direction"] == {"code": "09.03.02", "name": "Информационные системы и технологии"}
    assert work["authors"][0]["kind"] == "graduate" and work["authors"][0]["personId"]  # профиль заведён сам
    assert {(x["scheme"], x["code"]) for x in work["subjects"]} == {("grnti", "20.00"), ("oecd", "1.02")}
    assert work["fieldMeta"]["subjects"] == {"source": "direction"}
    assert work["hasFile"]

    draft = await works().get(uuid.UUID(detail["items"][2]["workId"]))
    assert draft["status"] == "draft" and draft["type"] == "coursework" and draft["kind"] == "project"
    assert draft["issues"] == ["ИИ не уверен: Авторы", "Не распознан автор: Сидоров С. С."]

    # «Повторить»: файл с ошибкой снова уходит воркеру
    detail = await service.retry(imp.id)
    assert detail["import"]["status"] == "processing" and detail["items"][1]["status"] == "pending"
    assert [dto.item_id for _, dto in publisher.published] == [items[1].id]
    with pytest.raises(BadRequestError):
        await service.retry(imp.id)  # ошибок больше нет


async def test_unknown_department_leaves_no_ai_badge(demo: dict) -> None:
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), FakePublisher())
    imp = Import(
        id=uuid.uuid7(), kind="archive", work_type="vkr", file_name="b.zip", status="processing", created_by="Модератор"
    )
    item = ImportItem(id=uuid.uuid7(), import_id=imp.id, n=1, ref="1.pdf", file_key=f"imports/{imp.id}/items/1.pdf")
    service.storage.objects[item.file_key] = b"%PDF-1.4"
    async with uow_write_factory() as uow:
        uow.session.add(imp)
        await uow.session.flush()
        uow.session.add(item)
    fields = {"title": "ВКР без кафедры", "department": "Кафедра, которой нет", "year": 2026}
    meta = {k: {"source": "ai", "confidence": 0.85} for k in fields}
    await service.on_extracted(ItemExtractedDTO(item_id=item.id, fields=fields, field_meta=meta), DELIVERY)
    work = await works().get(uuid.UUID((await service.get(imp.id, None))["items"][0]["workId"]))
    assert work["department"] is None and "department" not in work["fieldMeta"]  # не «ИИ, 85%» у пустого поля


def test_routes_need_role() -> None:
    """Роутер без зависимости открыл бы /api/admin/imports всем."""
    routes = [r for r in import_router.routes if isinstance(r, APIRoute)]
    assert routes
    for r in routes:
        assert require_moderator in _deps(r.dependant), r.path


async def test_anonymous_gets_401_everywhere() -> None:
    await anonymous_everywhere(import_app, [import_router])
