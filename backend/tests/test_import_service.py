"""import_service: таблицы, zip, импорт таблицы и результат воркера по архиву."""

import io
import unicodedata
import uuid
import zipfile

import pytest
from fastapi import UploadFile
from fastapi.routing import APIRoute
from sqlalchemy import select

from admin_service.src.service.works_service import WorkService
from import_service.src.api import router as import_router
from import_service.src.main import app as import_app
from import_service.src.service import archive, records
from import_service.src.service.import_service import ImportService
from shared.auth.session import require_moderator
from shared.bus.consumer import Delivery
from shared.bus.contracts import ItemExtractedDTO
from shared.bus.outbox import OutboxRelay
from shared.exceptions import BadRequestError
from shared.imports import Import, ImportItem
from shared.imports.tables import TooManyRows, read_table
from shared.people import Person
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
    out, _ = archive.extract_pdfs(path, tmp_path)
    assert [name for name, _ in out] == ["Иванов_ВКР.pdf"]
    assert out[0][1] == tmp_path / "1.pdf"  # имя из архива в путь не попало


def test_zip_limits(tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_zip({f"{i}.pdf": b"%PDF-1" for i in range(3)}).getvalue())
    monkeypatch.setattr(archive, "MAX_FILES", 2)
    with pytest.raises(archive.ArchiveError):
        archive.extract_pdfs(path, tmp_path)
    monkeypatch.setattr(archive, "MAX_FILES", 10)
    with pytest.raises(archive.ArchiveError):  # лимит на всю загрузку: прошлые zip уже дали 8 файлов
        archive.extract_pdfs(path, tmp_path, files=8)
    monkeypatch.setattr(archive, "MAX_TOTAL", 10)
    with pytest.raises(archive.ArchiveError, match="2 ГБ"):
        archive.extract_pdfs(path, tmp_path)
    monkeypatch.setattr(archive, "MAX_TOTAL", 20)
    with pytest.raises(archive.ArchiveError, match="2 ГБ"):  # 6 байт этого zip поверх 15 от прошлых
        archive.extract_pdfs(path, tmp_path, used=15)


def _raw_names_zip(*names: bytes) -> bytes:
    """zip с байтами имён как есть и без флага UTF-8 — так пишут 7-Zip (cp866) и Mac (UTF-8).
    zipfile сам так не запишет: не-ASCII имя он кодирует в UTF-8 и ставит флаг. Пишем ASCII-заглушку той же длины."""
    buf = io.BytesIO()
    stubs = [chr(ord("a") + i).encode() * len(raw) for i, raw in enumerate(names)]
    with zipfile.ZipFile(buf, "w") as zf:
        for stub in stubs:
            zf.writestr(stub.decode(), b"%PDF-1")
    data = buf.getvalue()
    for stub, raw in zip(stubs, names, strict=True):
        data = data.replace(stub, raw)  # в локальном заголовке и в центральном каталоге
    return data


def test_zip_names_from_windows_and_mac(tmp_path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_raw_names_zip("Иванов.pdf".encode("cp866"), "Йошкин.pdf".encode()))
    out, _ = archive.extract_pdfs(path, tmp_path)
    assert [name for name, _ in out] == ["Иванов.pdf", "Йошкин.pdf"]
    with zipfile.ZipFile(path := tmp_path / "b.zip", "w") as zf:
        zf.writestr(unicodedata.normalize("NFD", "Ёлкин.pdf"), b"%PDF-1")  # Mac: «Ё» двумя символами
    out, _ = archive.extract_pdfs(path, tmp_path)
    assert [name for name, _ in out] == ["Ёлкин.pdf"]


def test_unreadable_zip_entry_is_user_error(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.pdf", b"%PDF-1")
    data = bytearray(buf.getvalue())
    data[6] |= 1  # флаг «зашифровано» в локальном заголовке
    data[data.find(b"PK\x01\x02") + 8] |= 1  # и в центральном каталоге
    path = tmp_path / "a.zip"
    path.write_bytes(bytes(data))
    with pytest.raises(archive.ArchiveError, match="с паролем"):
        archive.extract_pdfs(path, tmp_path)


def test_table_rows_keep_excel_numbers_and_cap() -> None:
    rows = read_table("t.csv", "Авторы;Название\n\nА;Первая\n".encode())
    assert rows == [(1, ["Авторы", "Название"]), (3, ["А", "Первая"])]
    with pytest.raises(TooManyRows):
        read_table("t.csv", "Авторы;Название\nА;1\nА;2\nА;3\n".encode(), max_rows=2)
    assert len(read_table("t.csv", "Авторы;Название\nА;1\nА;2\n".encode(), max_rows=2)) == 3


def test_isbn10_and_talk_year_column() -> None:
    row = {"Название": "T", "Авторы": "А", "Год": "2024", "Вид": "Монография", "Журнал/сборник": ""}
    fields, meta, _ = records.publication(row | {"ISSN/ISBN": "5-02-013850-9"})
    assert (fields["isbn"], fields["issn"], "isbn" in meta) == ("5-02-013850-9", None, True)
    fields, _, _ = records.publication(row | {"ISSN/ISBN": "ISSN 1234-567X"})
    assert (fields["isbn"], fields["issn"]) == (None, "ISSN 1234-567X")
    assert records.column("talk", "year") == "Дата начала" and records.column("publication", "year") == "Год"


async def test_table_import(demo: dict) -> None:
    rows = (
        "Авторы;Название;Вид;Журнал/сборник;Год;DOI\n"
        '"Орлов Д. С.; Неизвестный А. А.";Новая статья о графах;Статья в журнале;Журнал;2026;\n'
        "Орлов Д. С.;Без года;Тезисы;Сборник;;\n"
        "Орлов Д. С.;Ссылка;Тезисы;Сборник;20x6;\n"
    )
    service = ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), relay(FakePublisher())
    )
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


def relay(publisher: FakePublisher) -> OutboxRelay:
    return OutboxRelay(uow_write_factory, publisher)


async def item_ids(import_id: uuid.UUID) -> list[uuid.UUID]:
    async with uow_write_factory() as uow:
        return list(await uow.session.scalars(select(ImportItem.id).where(ImportItem.import_id == import_id)))


def sent(publisher: FakePublisher) -> list[uuid.UUID]:
    """id элементов в событиях import_item.created, ушедших из outbox."""
    return [uuid.UUID(payload["item_id"]) for _, payload in publisher.published]


async def test_archive_item_from_worker(demo: dict) -> None:
    storage, publisher = FakeStorage(), FakePublisher()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), storage, relay(publisher))
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
    assert await service.outbox.flush() and sent(publisher) == [items[1].id]
    with pytest.raises(BadRequestError):
        await service.retry(imp.id)  # ошибок больше нет


async def test_unknown_department_leaves_no_ai_badge(demo: dict) -> None:
    service = ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), relay(FakePublisher())
    )
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


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(data), filename=name, size=len(data))


META = (
    "Имя файла;ФИО автора;Тема;ФИО руководителя;Год;Уровень;Код направления;Кафедра;Дисциплина;Работа или проект\n"
    ";;;;;;;;;\n"
    "Табличный.pdf;Табличный Студент Тестович;;Белова Наталья Викторовна;2026;бакалавриат;09.03.02;"
    "Кафедра информационных систем;Базы данных;проект\n"
)


async def test_archive_upload_with_meta_table(demo: dict) -> None:
    storage, publisher = FakeStorage(), FakePublisher()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), storage, relay(publisher))
    zipped = _raw_names_zip("папка/Табличный.pdf".encode("cp866"))  # как пишет 7-Zip на русской Windows
    files = [_upload("Архив.zip", zipped), _upload("Отдельный.pdf", b"%PDF-1.4")]

    bad = _upload("meta.csv", "Имя файла;Тема\nx.pdf;y\n".encode())
    with pytest.raises(BadRequestError, match="нет обязательных колонок"):
        await service.create("archive", "vkr", files, bad, demo["user"])
    twice = META + META.splitlines()[-1] + "\n"
    with pytest.raises(BadRequestError, match="повторяется"):
        await service.create("archive", "vkr", files, _upload("meta.csv", twice.encode()), demo["user"])
    assert storage.objects == {} and publisher.published == []  # отказ — ничего не залито и не отправлено

    imp = await service.create("archive", "vkr", files, _upload("meta.csv", META.encode()), demo["user"])
    assert (imp["fileName"], imp["metaTableName"]) == ("Архив.zip и ещё 1", "meta.csv")
    detail = await service.get(uuid.UUID(imp["id"]), None)
    assert [i["ref"] for i in detail["items"]] == ["Табличный.pdf", "Отдельный.pdf"]
    assert await service.outbox.flush() and len(sent(publisher)) == 2
    assert not any(k.endswith(".zip") for k in storage.objects)

    # ИИ «уверен» в другом ФИО и узнал курсовую: таблица ВКР её дисциплину не теряет, а профиль под ФИО от ИИ не заводится
    item_id = sent(publisher)[0]
    fields = {"title": "Тема от ИИ", "authors": ["Выдуманный Автор Ии"], "year": 2026, "workType": "coursework"}
    meta = {k: {"source": "ai", "confidence": 0.85} for k in ("title", "authors", "year")}
    await service.on_extracted(ItemExtractedDTO(item_id=item_id, fields=fields, field_meta=meta), DELIVERY)
    detail = await service.get(uuid.UUID(imp["id"]), None)
    work = await works().get(uuid.UUID(detail["items"][0]["workId"]))
    assert work["title"] == "Тема от ИИ" and work["fieldMeta"]["title"]["source"] == "ai"  # пустая ячейка не стёрла
    assert [a["displayName"] for a in work["authors"]] == ["Табличный С. Т."] and work["authors"][0]["personId"]
    assert (work["type"], work["discipline"], work["kind"]) == ("coursework", "Базы данных", "project")
    async with uow_write_factory() as uow:
        assert await uow.session.scalar(select(Person).where(Person.full_name == "Выдуманный Автор Ии")) is None


class BrokenStorage(FakeStorage):
    async def copy(self, source: str, key: str) -> None:
        raise ConnectionError("MinIO недоступен")


class BrokenPublisher(FakePublisher):
    async def publish(self, topic, dto, **_) -> None:
        if self.published:
            raise ConnectionError("RabbitMQ недоступен")
        await super().publish(topic, dto)


async def test_bus_down_keeps_events_in_outbox(demo: dict) -> None:
    """Шина приняла одно событие из трёх: остальные ждут в outbox и уходят, когда она вернётся. Элементы — в очереди."""
    broken = BrokenPublisher()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), BrokenStorage(), relay(broken))
    files = [_upload(f"{n}.pdf", b"%PDF-1.4") for n in (1, 2, 3)]
    imp = await service.create("archive", "vkr", files, None, demo["user"])
    assert await service.outbox.flush() is False and len(sent(broken)) == 1
    detail = await service.get(uuid.UUID(imp["id"]), None)
    assert [i["status"] for i in detail["items"]] == ["pending"] * 3

    service.outbox.publisher = healthy = FakePublisher()
    assert await service.outbox.flush() and len(sent(healthy)) == 2
    assert set(sent(broken) + sent(healthy)) == set(await item_ids(uuid.UUID(imp["id"])))
    assert await service.outbox.flush() and len(sent(healthy)) == 2  # опубликованное из outbox удалено

    # не сохранился результат воркера — элемент в ошибку, «Повторить» его увидит
    fields = {"title": "ВКР, которую не сохранить", "year": 2026}
    meta = {k: {"source": "ai", "confidence": 0.85} for k in fields}
    await service.on_extracted(ItemExtractedDTO(item_id=sent(broken)[0], fields=fields, field_meta=meta), DELIVERY)
    detail = await service.get(uuid.UUID(imp["id"]), None)
    assert detail["items"][0]["error"] == "Не удалось сохранить результат разбора"


async def test_preview_and_table_refs_match_excel_lines(demo: dict) -> None:
    service = ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), relay(FakePublisher())
    )
    rows = "Авторы;Название;Вид;Журнал/сборник;Год\n\nОрлов Д. С.;Строка через пустую;Тезисы;Сборник;2026\n"
    preview = await service.preview("publication", _upload("p.csv", rows.encode()))
    assert [r["line"] for r in preview["rows"]] == [3]
    imp = await service.create("table", "publication", [_upload("p.csv", rows.encode())], None, demo["user"])
    detail = await service.get(uuid.UUID(imp["id"]), "filled")
    assert [i["ref"] for i in detail["items"]] == ["Строка 3"]
    assert (await service.get(uuid.UUID(imp["id"]), "published"))["items"] == []


def test_routes_need_role() -> None:
    """Роутер без зависимости открыл бы /api/admin/imports всем."""
    routes = [r for r in import_router.routes if isinstance(r, APIRoute)]
    assert routes
    for r in routes:
        assert require_moderator in _deps(r.dependant), r.path


async def test_anonymous_gets_401_everywhere() -> None:
    await anonymous_everywhere(import_app, [import_router])


SCOPUS_BIB = r"""
@ARTICLE{Oseledets2011,
  author = {Oseledets, I. V. and Tyrtyshnikov, {E. E.}},
  title = {{Tensor-Train} Decomposition},
  journal = {SIAM Journal on Scientific Computing},
  year = {2011},
  volume = {33},
  number = {5},
  pages = {2295--2317},
  doi = {10.1137/090752286},
  url = {https://www.scopus.com/inward/record.uri?eid=2-s2.0-1},
  publisher = "SIAM",
  source = {Scopus},
}
@comment{пропускается}
@book{Ivanov2020, author = {Иванов, Иван Петрович}, title = {Базы данных \& SQL}, publisher = {Наука}, year = 2020}
"""

WOS_RIS = """TY  - JOUR
AU  - Rakhuba, Maxim
AU  - Oseledets, Ivan V.
TI  - Fast multidimensional convolution
T2  - JOURNAL OF COMPUTATIONAL PHYSICS
PY  - 2016
VL  - 304
SP  - 167
EP  - 175
DO  - 10.1016/j.jcp.2015.10.015
AN  - WOS:000365030700011
ER  -

TY  - CPAPER
AU  - Smith, John
TI  - Zotero paper
BT  - Proceedings of Something
PY  - 2024/05/01
ER  -
"""


def test_bibtex_and_ris_become_template_rows() -> None:
    from shared.imports.bibliography import parse_bibtex, parse_ris

    article, book = parse_bibtex(SCOPUS_BIB)
    assert article["Авторы"] == "Oseledets I. V.; Tyrtyshnikov E. E."
    assert (article["Название"], article["Вид"], article["Страницы"]) == (
        "Tensor-Train Decomposition",
        "journal_article",
        "2295–2317",
    )
    assert (article["Журнал/сборник"], article["Номер"], article["DOI"], article["Индексация"]) == (
        "SIAM Journal on Scientific Computing",
        "5",
        "10.1137/090752286",
        "Scopus",
    )
    assert article["Издательство"] == "SIAM"
    assert (book["Авторы"], book["Название"], book["Вид"], book["Год"]) == (
        "Иванов И. П.",
        "Базы данных & SQL",
        "monograph",
        "2020",
    )

    wos, zotero = parse_ris(WOS_RIS)
    assert wos["Авторы"] == "Rakhuba M.; Oseledets I. V." and wos["Страницы"] == "167–175"
    assert (wos["Журнал/сборник"], wos["Индексация"], wos["Вид"]) == (
        "JOURNAL OF COMPUTATIONAL PHYSICS",
        "WoS",
        "journal_article",
    )
    assert (zotero["Вид"], zotero["Журнал/сборник"], zotero["Год"], zotero["Индексация"]) == (
        "proceedings_paper",
        "Proceedings of Something",
        "2024",
        "",
    )


async def test_bibliography_import_makes_drafts(demo: dict) -> None:
    service = ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), relay(FakePublisher())
    )
    with pytest.raises(BadRequestError, match="как публикации"):
        await service.create("table", "talk", [_upload("talks.bib", SCOPUS_BIB.encode())], None, demo["user"])
    preview = await service.preview("publication", _upload("scopus.bib", SCOPUS_BIB.encode()))
    assert (preview["total"], preview["bad"], preview["missingColumns"]) == (2, 0, [])  # монографии журнал не нужен
    imp = await service.create("table", "publication", [_upload("scopus.bib", SCOPUS_BIB.encode())], None, demo["user"])
    detail = await service.get(uuid.UUID(imp["id"]), None)
    assert [i["ref"] for i in detail["items"]] == ["Запись 1", "Запись 2"]
    assert [i["status"] for i in detail["items"]] == ["filled", "filled"]
    work = await works().get(uuid.UUID(detail["items"][0]["workId"]))
    assert work["indexing"] == ["scopus"] and work["fieldMeta"]["title"] == {"source": "bibliography"}
    book = await works().get(uuid.UUID(detail["items"][1]["workId"]))
    assert (book["kind"], book["publisher"], book["errors"]) == ("monograph", "Наука", [])


def test_text_list_split_into_entries() -> None:
    elibrary = (
        "1. НЕЙРОННЫЕ СЕТИ ДЛЯ ГРАФОВ\nОрлов Д.С., Белова Н.В.\nВестник университета. 2024. Т. 3. № 2. С. 10-20.\n"
        "2. ВТОРАЯ СТАТЬЯ\nОрлов Д.С.\nСборник трудов. 2023. С. 5-6.\n"
    )
    assert records.split_entries(elibrary) == [
        "НЕЙРОННЫЕ СЕТИ ДЛЯ ГРАФОВ Орлов Д.С., Белова Н.В. Вестник университета. 2024. Т. 3. № 2. С. 10-20.",
        "ВТОРАЯ СТАТЬЯ Орлов Д.С. Сборник трудов. 2023. С. 5-6.",
    ]
    assert records.split_entries("Первая\nзапись\n\nВторая запись\n") == ["Первая запись", "Вторая запись"]
    assert records.split_entries("Одна\nДругая\n  \n") == ["Одна", "Другая"]
    assert records.split_entries("   ") == [] and records.split_entries('" "\n—') == []


async def test_text_list_import_goes_through_ai(demo: dict) -> None:
    publisher = FakePublisher()
    service = ImportService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage(), relay(publisher))
    with pytest.raises(BadRequestError, match="ни одной записи"):
        await service.create("text", "publication", [], None, demo["user"], " \n ")
    text = "1. Орлов Д. С. Графовые сети для каталога // Вестник. 2025. № 1. С. 1–9.\n2. Запись, которую ИИ не разберёт"
    imp = await service.create("text", "publication", [], None, demo["user"], text)
    assert imp["fileName"] == "Список текстом: 2 записи" and imp["status"] == "processing"
    assert await service.outbox.flush()
    sent_dtos = [p for _, p in publisher.published]
    assert [d["text"] for d in sent_dtos] == [
        "Орлов Д. С. Графовые сети для каталога // Вестник. 2025. № 1. С. 1–9.",
        "Запись, которую ИИ не разберёт",
    ]
    assert all(d["file_key"] is None for d in sent_dtos)

    first, second = (uuid.UUID(d["item_id"]) for d in sent_dtos)
    record = {
        "Авторы": "Орлов Д. С.",
        "Название": "Графовые сети для каталога",
        "Вид": "Статья в журнале",
        "Журнал/сборник": "Вестник",
        "Год": "2025",
        "Номер": "1",
        "Страницы": "1–9",
    }
    meta = {c: {"source": "ai", "confidence": 0.85} for c in record} | {"Вид": {"source": "ai", "confidence": 0.4}}
    await service.on_extracted(ItemExtractedDTO(item_id=first, fields={"record": record}, field_meta=meta), DELIVERY)
    await service.on_extracted(
        ItemExtractedDTO(item_id=second, fields={"record": {"Название": "Только название"}}, field_meta={}), DELIVERY
    )
    detail = await service.get(uuid.UUID(imp["id"]), None)
    assert detail["import"]["status"] == "done"
    assert [i["status"] for i in detail["items"]] == ["filled", "error"]
    assert detail["items"][1]["error"].startswith("Не указан")
    work = await works().get(uuid.UUID(detail["items"][0]["workId"]))
    assert (work["status"], work["kind"], work["venue"], work["issue"]) == ("draft", "journal_article", "Вестник", "1")
    assert work["authors"][0]["personId"] == str(demo["ids"]["p-s1"])  # Орлов из базы
    assert work["fieldMeta"]["title"] == {"source": "ai", "confidence": 0.85}
    assert work["fieldMeta"]["kind"]["confidence"] == 0.4 and work["fieldMeta"]["kind"]["lowConfidence"]

    detail = await service.retry(uuid.UUID(imp["id"]))  # ошибка записи текстом — снова воркеру, с тем же текстом
    assert await service.outbox.flush() and publisher.published[-1][1]["text"] == "Запись, которую ИИ не разберёт"
