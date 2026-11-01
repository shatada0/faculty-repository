"""Импорт: таблицы публикаций и докладов — синхронно, архивы ВКР и курсовых — через шину и extraction_worker."""

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import func, or_, select, update

from import_service.src.service import records
from import_service.src.service.archive import MAX_FILES, ArchiveError, display_name, extract_pdfs
from shared.auth.session import SessionUser
from shared.bus.consumer import Delivery
from shared.bus.contracts import ImportTopic, ItemCreatedDTO, ItemExtractedDTO
from shared.bus.outbox import OutboxRelay, enqueue
from shared.config import UPLOAD_LIMITS
from shared.db.base import now
from shared.exceptions import BadRequestError, NotFoundError
from shared.imports import ExtractionResult, Import, ImportItem
from shared.imports.bibliography import is_bibliography
from shared.imports.tables import (
    MAX_ROWS,
    TEMPLATES,
    Row,
    TableError,
    TooManyRows,
    missing_columns,
    missing_in_row,
    read_table,
    to_records,
)
from shared.logging import get_logger
from shared.people import Person
from shared.people.names import short_name
from shared.refdata import Department, Direction
from shared.repository.people_repository import PersonRepository, add_name
from shared.repository.works_repository import WorkRepository
from shared.storage import Storage, detect_type
from shared.uow import UnitOfWork, UnitOfWorkFactory
from shared.works import Work
from shared.works import labels as L
from shared.works.checks import check_work
from shared.works.schemas import apply_write, parse_write

logger = get_logger(__name__)

TABLE_ERROR = "Не удалось прочитать таблицу. Проверьте, что это .xlsx или .csv."
CONFIRMED = 0.95  # значение ИИ подтвердилось по базе
IN_TEXT = 0.85  # значение ИИ дословно есть в тексте PDF: такому ФИО можно завести профиль
STATS = ("total", "processed", "filled", "published", "duplicates", "errors", "unmatched")
NAME_COLUMNS = {"publication": ("Авторы",), "talk": ("Докладчики", "Соавторы")}
NAMES_PER_QUERY = 5000  # asyncpg держит до 32767 параметров, а у имени их два ключа


def import_dict(imp: Import, stats: dict[str, int]) -> dict:
    return {
        "id": str(imp.id),
        "kind": imp.kind,
        "workType": imp.work_type,
        "fileName": imp.file_name,
        **({"metaTableName": imp.meta_table_name} if imp.meta_table_name else {}),
        "status": imp.status,
        "createdAt": imp.created_at.isoformat(),
        "createdBy": imp.created_by,
        "stats": stats,
    }


def _short(entry: str) -> str:
    """Запись списка в колонке «Файл / строка»: первые 120 знаков одной строкой."""
    flat = " ".join(entry.split())
    return flat if len(flat) <= 120 else flat[:119] + "…"


def item_dict(it: ImportItem) -> dict:
    out = {
        "ref": it.ref,
        "title": it.title,
        "status": it.status,
        "error": it.error,
        "workId": str(it.work_id) if it.work_id else None,
        "duplicateOf": str(it.duplicate_of) if it.duplicate_of else None,
        "unmatchedAuthors": it.unmatched_authors or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def _stats_columns() -> tuple:
    count = lambda cond: func.count().filter(cond)  # noqa: E731
    return (
        func.count(ImportItem.id).label("total"),
        count(ImportItem.status.not_in(("pending", "extracted"))).label("processed"),
        count(ImportItem.status == "filled").label("filled"),
        count(ImportItem.status == "published").label("published"),
        count(ImportItem.status == "duplicate").label("duplicates"),
        count(ImportItem.status == "error").label("errors"),
        count(func.cardinality(ImportItem.unmatched_authors) > 0).label("unmatched"),
    )


def _check_file(file: UploadFile, rule: str) -> None:
    limits = UPLOAD_LIMITS[rule]
    name = (file.filename or "").lower()
    if not any(name.endswith(ext) for ext in limits["accept"]) or (file.size or 0) > limits["maxBytes"]:
        raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")


async def _table(file: UploadFile) -> tuple[list[str], list[Row]]:
    """→ (заголовок, непустые строки данных с номерами, как в Excel)."""
    _check_file(file, "table")
    try:
        rows = await asyncio.to_thread(read_table, file.filename or "", await file.read())
    except TooManyRows as e:
        raise BadRequestError(f"В таблице больше {MAX_ROWS} строк — разбейте её на части", code="bad_table") from e
    except TableError as e:
        raise BadRequestError(TABLE_ERROR, code="bad_table") from e
    return (rows[0][1], rows[1:]) if rows else ([], [])


def _need_columns(header: list[str], work_type: str, what: str = "В таблице") -> None:
    if missing := missing_columns(header, work_type):
        raise BadRequestError(
            f"{what} нет обязательных колонок: {', '.join(missing)}. Скачайте шаблон.", code="bad_table"
        )


async def _meta_table(file: UploadFile, work_type: str) -> dict[str, dict[str, str]]:
    """Таблица сведений к архиву: имя файла → строка. Колонки — по шаблону курсовых, он шире шаблона ВКР:
    воркер может узнать курсовую и в импорте ВКР, и её «Дисциплина» не должна потеряться."""
    header, body = await _table(file)
    _need_columns(header, work_type, "В таблице сведений")
    by_file: dict[str, dict[str, str]] = {}
    for (line, _), record in zip(body, to_records(header, [c for _, c in body], "coursework"), strict=True):
        if not record["Имя файла"]:
            continue
        name = display_name(record["Имя файла"]).lower()
        if name in by_file:
            raise BadRequestError(
                f"В таблице сведений имя файла «{record['Имя файла']}» повторяется (строка {line})", code="bad_table"
            )
        by_file[name] = record
    return by_file


def _copy(src: IO[bytes], path: Path) -> None:
    with path.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def _bad_value(e: ValidationError, work_type: str) -> str:
    loc = ".".join(str(x) for x in e.errors()[0]["loc"])
    return f"Неверное значение: {records.column(work_type, loc)}"


def _ref(name: str, person: Person | None, fallback: str = "external") -> dict:
    if person:
        return {"personId": str(person.id), "displayName": name, "kind": person.kind}
    return {"personId": None, "displayName": name, "kind": fallback}


@dataclass(slots=True)
class ImportService:
    uow_factory: UnitOfWorkFactory
    works: WorkRepository
    people: PersonRepository
    storage: Storage
    outbox: OutboxRelay

    # ---------- чтение ----------

    async def list(self) -> list[dict]:
        async with self.uow_factory() as uow:
            rows = (
                await uow.session.execute(
                    select(Import, *_stats_columns())
                    .outerjoin(ImportItem, ImportItem.import_id == Import.id)
                    .group_by(Import.id)
                    .order_by(Import.created_at.desc())
                )
            ).all()
        return [
            import_dict(
                r.Import,
                {k: getattr(r, k) for k in STATS},
            )
            for r in rows
        ]

    async def get(self, import_id: UUID, status: str | None) -> dict:
        async with self.uow_factory() as uow:
            imp = await uow.session.get(Import, import_id)
            if imp is None:
                raise NotFoundError("Импорт не найден")
            stats = (
                await uow.session.execute(select(*_stats_columns()).where(ImportItem.import_id == import_id))
            ).one()
            stmt = select(ImportItem).where(ImportItem.import_id == import_id).order_by(ImportItem.n)
            if status:
                stmt = stmt.where(ImportItem.status == status)
            items = (await uow.session.execute(stmt)).scalars().all()
        return {"import": import_dict(imp, stats._asdict()), "items": [item_dict(it) for it in items]}

    async def retry(self, import_id: UUID) -> dict:
        """Файлы с ошибкой — снова воркеру: ИИ был недоступен, оборвалась сеть. Битый PDF просто упадёт ещё раз."""
        async with self.uow_factory() as uow:
            imp = await uow.session.get(Import, import_id, with_for_update=True)
            if imp is None:
                raise NotFoundError("Импорт не найден")
            items = (
                (
                    await uow.session.execute(
                        select(ImportItem).where(
                            ImportItem.import_id == import_id,
                            ImportItem.status == "error",
                            or_(ImportItem.file_key.is_not(None), ImportItem.text.is_not(None)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not items:
                raise BadRequestError("Повторять нечего: файлов с ошибкой нет", code="nothing_to_retry")
            for it in items:
                it.status, it.error = "pending", None
            imp.status = "processing"
            self._publish(uow, imp.work_type, items)
        self.outbox.kick()
        logger.info("Повтор импорта", extra={"import_id": str(import_id), "files": len(items)})
        return await self.get(import_id, None)

    async def preview(self, work_type: str, file: UploadFile) -> dict:
        """Заголовки, первые 10 строк с незаполненными обязательными полями, сколько строк и сколько плохих."""
        header, body = await _table(file)
        missing = [missing_in_row(r, work_type) for r in to_records(header, [c for _, c in body], work_type)]
        return {
            "header": header,
            "rows": [
                {"line": line, "cells": cells, "missing": missing[i]} for i, (line, cells) in enumerate(body[:10])
            ],
            "total": len(body),
            "bad": sum(1 for m in missing if m),
            "missingColumns": missing_columns(header, work_type),
        }

    # ---------- создание ----------

    async def _match(self, uow: UnitOfWork, names: list[str]) -> dict[str, Person]:
        """Все ФИО загрузки — пачками, а не запрос на каждого автора каждой строки."""
        unique = list(dict.fromkeys(names))
        found: dict[str, Person] = {}
        for i in range(0, len(unique), NAMES_PER_QUERY):
            found |= await self.people.match_many(uow, unique[i : i + NAMES_PER_QUERY])
        return found

    async def _draft(
        self, uow: UnitOfWork, imp: Import, fields: dict[str, Any], meta: dict, work_type: str = "", **extra: Any
    ) -> tuple[Work, list[str]]:
        """Черновик импорта; дубль ищется по (type, название, год). → (работа, нераспознанные авторы).

        work_type — тип, который воркер узнал по титульнику; без него — тип импорта.
        Поиск (search) пересчитывает вызывающий: у таблицы — одним запросом на все строки.
        """
        work_type = work_type or imp.work_type
        model = parse_write(work_type, fields)
        w = Work(
            type=work_type,
            status="draft",
            import_id=imp.id,
            details={},
            keywords=[],
            people=[],  # ИИ не нашёл ни одного ФИО — без этого w.people ниже пошёл бы лениво в базу и упал
            field_meta=meta,
            **{"id": uuid.uuid7(), **extra},
        )
        apply_write(w, model)
        duplicate = await self.works.find_duplicate(uow, w.type, w.title, w.year)
        w.duplicate_of = duplicate.id if duplicate else None
        w.department = await self.works.known_department(uow, w.department)  # нет в справочнике — выберет модератор
        uow.session.add(w)
        await uow.session.flush()  # следующая строка той же таблицы найдёт эту работу как дубль
        unmatched = list(dict.fromkeys(p.display_name for p in w.people if p.person_id is None and p.display_name))
        return w, unmatched

    async def create(
        self,
        kind: str,
        work_type: str,
        files: list[UploadFile],
        meta_table: UploadFile | None,
        user: SessionUser,
        text: str | None = None,
    ) -> dict:
        if kind == "text" and work_type in ("publication", "talk"):
            import_id = await self._create_text(work_type, text or "", user)
        elif kind == "table" and work_type in ("publication", "talk"):
            if len(files) != 1:
                raise BadRequestError("Таблица импортируется одним файлом")
            import_id = await self._create_table(work_type, files[0], user)
        elif kind == "archive" and work_type in ("vkr", "coursework"):
            import_id = await self._create_archive(work_type, files, meta_table, user)
        else:
            raise BadRequestError(
                "Таблицей и списком текстом импортируются публикации и доклады, архивом — ВКР и курсовые"
            )
        return (await self.get(import_id, None))["import"]

    async def _create_table(self, work_type: str, file: UploadFile, user: SessionUser) -> UUID:
        """csv/xlsx по шаблону или BibTeX/RIS (только публикации): записи библиографии читаются как строки шаблона."""
        bib = is_bibliography(file.filename or "")
        if bib and work_type != "publication":
            raise BadRequestError("BibTeX и RIS импортируются как публикации", code="bad_table")
        header, body = await _table(file)
        _need_columns(header, work_type)
        rows = to_records(header, [c for _, c in body], work_type)
        source: records.Source = (lambda _: {"source": "bibliography"}) if bib else records.from_table
        async with self.uow_factory() as uow:
            imp = Import(
                id=uuid.uuid7(),
                kind="table",
                work_type=work_type,
                file_name=display_name(file.filename or "table"),
                status="done",
                created_by=user.name,
            )
            uow.session.add(imp)
            found = await self._match(
                uow, [name for r in rows for col in NAME_COLUMNS[work_type] for name in records.split_names(r[col])]
            )
            work_ids = []
            for n, ((line, _), record) in enumerate(zip(body, rows, strict=True), start=1):
                item = ImportItem(
                    import_id=imp.id,
                    n=n,
                    ref=f"{'Запись' if bib else 'Строка'} {line}",
                    title=record.get("Название") or record.get("Название доклада") or None,
                )
                uow.session.add(item)
                if work_id := await self._row(uow, imp, item, record, found, source, created_by=user.id):
                    work_ids.append(work_id)
            await self.works.refresh_search(uow, work_ids)
        logger.info("Импорт таблицы", extra={"import_id": str(imp.id), "rows": len(rows)})
        return imp.id

    async def _row(
        self,
        uow: UnitOfWork,
        imp: Import,
        item: ImportItem,
        record: dict[str, str],
        found: dict[str, Person],
        source: records.Source,
        **extra: Any,
    ) -> UUID | None:
        """Строка шаблона (таблица, BibTeX/RIS или ответ ИИ на запись текстом) → черновик. → id работы или None."""
        work_type = imp.work_type
        if miss := missing_in_row(record, work_type):
            item.status, item.error = "error", records.row_error(miss)
            return None
        if work_type == "publication":
            fields, meta, authors = records.publication(record, source)
            fields["authors"] = [_ref(a, found.get(a)) for a in authors]
        else:
            fields, meta, presenters, coauthors = records.talk(record, source)
            fields["presenters"] = [_ref(p, found.get(p)) for p in presenters]
            fields["authors"] = [*fields["presenters"], *(_ref(c, found.get(c)) for c in coauthors)]
        try:  # parse_write падает до первой записи в сессию: строка с ошибкой не роняет весь импорт
            w, unmatched = await self._draft(uow, imp, fields, meta, **extra)
        except ValidationError as e:
            item.status, item.error = "error", _bad_value(e, work_type)
            return None
        item.status = "duplicate" if w.duplicate_of else "filled"
        item.work_id, item.duplicate_of, item.title, item.unmatched_authors = w.id, w.duplicate_of, w.title, unmatched
        return w.id

    async def _create_text(self, work_type: str, text: str, user: SessionUser) -> UUID:
        """Список работ текстом — скопированный из eLIBRARY, отчёта или программы конференции: каждую запись
        разбирает ИИ в строку шаблона, дальше — как строка таблицы. Черновики ждут модератора."""
        entries = records.split_entries(text)
        if not entries:
            raise BadRequestError("В тексте нет ни одной записи", code="bad_text")
        if len(entries) > MAX_FILES:
            raise BadRequestError(f"За один раз — не больше {MAX_FILES} записей", code="bad_text")
        async with self.uow_factory() as uow:
            imp = Import(
                id=uuid.uuid7(),
                kind="text",
                work_type=work_type,
                file_name=f"Список текстом: {len(entries)} {L.plural(len(entries), ('запись', 'записи', 'записей'))}",
                status="processing",
                created_by=user.name,
            )
            uow.session.add(imp)
            await uow.session.flush()
            items = [
                ImportItem(id=uuid.uuid7(), import_id=imp.id, n=n, ref=_short(entry), text=entry)
                for n, entry in enumerate(entries, start=1)
            ]
            uow.session.add_all(items)
            self._publish(uow, work_type, items)
        self.outbox.kick()
        logger.info("Список текстом принят", extra={"import_id": str(imp.id), "entries": len(entries)})
        return imp.id

    async def _create_archive(
        self, work_type: str, files: list[UploadFile], meta_table: UploadFile | None, user: SessionUser
    ) -> UUID:
        """PDF и zip вперемешку, сколько угодно файлов в одной загрузке: каждый PDF — элемент импорта.

        Тип каждой работы (ВКР или курсовая) воркер узнаёт по титульнику; work_type — на случай, если не узнает.
        """
        for f in files:
            _check_file(f, "archive")
        if sum(f.size or 0 for f in files) > UPLOAD_LIMITS["archive"]["maxBytes"]:
            raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
        meta = meta_table if meta_table is not None and meta_table.size else None  # пустое поле формы — таблицы нет
        by_file = await _meta_table(meta, work_type) if meta else {}
        import_id = uuid.uuid7()
        items = []
        with tempfile.TemporaryDirectory() as tmp:
            pdfs: list[tuple[str, Path]] = []
            unpacked = 0  # байт из всех zip загрузки: лимит распаковки общий, а не на каждый архив
            for k, f in enumerate(files):
                if not (f.filename or "").lower().endswith(".zip"):
                    # .pdf берём как есть: не PDF внутри — воркер отметит элемент ошибкой, как и файл из zip
                    path = Path(tmp) / f"upload-{k}.pdf"
                    await asyncio.to_thread(_copy, f.file, path)
                    pdfs.append((display_name(f.filename or "file.pdf"), path))
                    continue
                if detect_type(await f.read(8)) != "zip":
                    raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
                await f.seek(0)
                folder = Path(tmp) / f"zip-{k}"
                folder.mkdir()
                try:
                    found, unpacked = await asyncio.to_thread(extract_pdfs, f.file, folder, len(pdfs), unpacked)
                except ArchiveError as e:
                    raise BadRequestError(f"{display_name(f.filename or 'archive.zip')}: {e}", code="bad_file") from e
                pdfs += found
            if len(pdfs) > MAX_FILES:
                raise BadRequestError(f"За один раз — не больше {MAX_FILES} работ", code="bad_file")
            for n, (name, path) in enumerate(pdfs, start=1):
                key = f"imports/{import_id}/items/{n}.pdf"  # имя файла в ключ не попадает
                with path.open("rb") as f:
                    await self.storage.put(key, f, "application/pdf")
                items.append(
                    ImportItem(
                        id=uuid.uuid7(),
                        import_id=import_id,
                        n=n,
                        ref=name,
                        file_key=key,
                        meta=by_file.get(name.lower()),
                    )
                )
        names = [display_name(f.filename or "file") for f in files]
        async with self.uow_factory() as uow:
            uow.session.add(
                Import(
                    id=import_id,
                    kind="archive",
                    work_type=work_type,
                    file_name=names[0] if len(names) == 1 else f"{names[0]} и ещё {len(names) - 1}",
                    meta_table_name=display_name(meta.filename or "table") if meta else None,
                    status="processing",
                    created_by=user.name,
                )
            )
            await uow.session.flush()
            uow.session.add_all(items)
            self._publish(uow, work_type, items)
        self.outbox.kick()
        logger.info(
            "Архив принят",
            extra={
                "import_id": str(import_id),
                "uploads": len(files),
                "files": len(items),
                "meta_matched": sum(1 for it in items if it.meta),
            },
        )
        return import_id

    @staticmethod
    def _publish(uow: UnitOfWork, work_type: str, items: Sequence[ImportItem]) -> None:
        """В той же транзакции, что и элементы: воркер не получит элемента, которого нет в базе,
        а элемент не останется «в очереди» без события, даже если шина лежит или сервис упал после коммита."""
        for it in items:
            dto = ItemCreatedDTO(item_id=it.id, file_key=it.file_key, work_type=work_type, text=it.text)
            enqueue(uow, ImportTopic.ITEM_CREATED, dto)

    # ---------- результат воркера ----------

    async def on_extracted(self, dto: ItemExtractedDTO, _: Delivery) -> None:
        """Черновик из результата ИИ. Повтор сообщения ничего не ломает: обрабатываем только pending.

        Упало сохранение (MinIO, база) — элемент в ошибку: иначе сообщение ушло бы в dead letters,
        а элемент навсегда остался бы в очереди, и «Повторить» его не видит.
        """
        try:
            await self._on_extracted(dto)
        except Exception:
            logger.exception("Результат воркера не сохранён", extra={"item_id": str(dto.item_id)})
            await self._fail([dto.item_id], "Не удалось сохранить результат разбора")

    async def _on_extracted(self, dto: ItemExtractedDTO) -> None:
        async with self.uow_factory() as uow:
            item = await uow.session.get(ImportItem, dto.item_id)
            if item is None or item.status != "pending":
                logger.info("Элемент уже обработан — пропуск", extra={"item_id": str(dto.item_id)})
                return
            # Блокировка импорта: два элемента одного импорта не решат одновременно, что они последние.
            imp = await self._lock(uow, item.import_id)
            await uow.session.refresh(item)
            if item.status != "pending":
                return
            if dto.error:
                item.status, item.error = "error", dto.error
            else:
                await self._fill_item(uow, imp, item, dto.fields or {}, dto.field_meta or {})
            await uow.session.flush()
            await self._finish(uow, imp)
        logger.info("Элемент импорта готов", extra={"item_id": str(dto.item_id), "status": item.status})

    @staticmethod
    async def _lock(uow: UnitOfWork, import_id: UUID) -> Import:
        return (await uow.session.execute(select(Import).where(Import.id == import_id).with_for_update())).scalar_one()

    @staticmethod
    async def _finish(uow: UnitOfWork, imp: Import) -> None:
        left = await uow.session.scalar(
            select(func.count()).where(ImportItem.import_id == imp.id, ImportItem.status == "pending")
        )
        if left == 0:
            imp.status = "done"

    async def _fail(self, item_ids: list[UUID], error: str) -> None:
        """Элементы, что ещё в очереди, — в ошибку; если в очереди больше никого, импорт готов."""
        async with self.uow_factory() as uow:
            import_id = await uow.session.scalar(select(ImportItem.import_id).where(ImportItem.id == item_ids[0]))
            if import_id is None:
                return
            imp = await self._lock(uow, import_id)
            await uow.session.execute(
                update(ImportItem)
                .where(ImportItem.id.in_(item_ids), ImportItem.status == "pending")
                .values(status="error", error=error)
            )
            await self._finish(uow, imp)

    async def _fill_item(
        self, uow: UnitOfWork, imp: Import, item: ImportItem, ai: dict[str, Any], ai_meta: dict
    ) -> None:
        """Черновик из ответа воркера; без замечаний — сразу опубликованная карточка (текст закрыт до согласия).
        Запись списка текстом — строка шаблона от ИИ: черновик, как из таблицы, без автопубликации."""
        if imp.kind == "text":
            # все колонки шаблона: чего ИИ не нашёл — пусто, как незаполненная ячейка
            record = {c: "" for c, _ in TEMPLATES[imp.work_type]} | (ai.get("record") or {})
            if imp.work_type == "talk" and not record["Уровень"]:  # в программах уровень редко пишут
                record["Уровень"] = L.CONF_LEVEL["national"]
            names = [n for col in NAME_COLUMNS[imp.work_type] for n in records.split_names(record.get(col, ""))]
            found = await self._match(uow, names)
            ai_source: records.Source = lambda col: ai_meta.get(col) or {"source": "ai", "confidence": 0.4}  # noqa: E731
            if work_id := await self._row(uow, imp, item, record, found, ai_source):
                await self.works.refresh_search(uow, [work_id])
            return
        work_type = ai.get("workType") if ai.get("workType") in ("vkr", "coursework") else imp.work_type
        try:
            # невалидный ответ откатит и профили студентов, заведённые под эту карточку
            async with uow.session.begin_nested():
                fields, meta = await self._fields(uow, item, ai, ai_meta, work_type)
                work_id = uuid.uuid7()
                w, unmatched = await self._draft(
                    uow,
                    imp,
                    fields,
                    meta,
                    work_type,
                    id=work_id,
                    file_key=f"works/{work_id}/{uuid.uuid4()}.pdf",
                    original_file_name=item.ref,
                )
        except ValidationError as e:
            item.status, item.error = "error", _bad_value(e, work_type)
            return
        for field, m in ai_meta.items():  # сырой ответ воркера: точность ИИ считается по нему
            uow.session.add(
                ExtractionResult(
                    work_id=w.id,
                    field="direction" if field == "directionCode" else field,
                    value=ai.get(field),
                    confidence=m.get("confidence", 0),
                )
            )
        await self.works.refresh_search(uow, [w.id])
        # копия последней: невалидный ответ не оставит сироту в works/, у которой нет lifecycle
        await self.storage.copy(item.file_key, w.file_key)  # imports/ удалит lifecycle через 30 дней
        item.status = "duplicate" if w.duplicate_of else "filled"
        item.work_id, item.duplicate_of, item.title, item.unmatched_authors = w.id, w.duplicate_of, w.title, unmatched
        if w.duplicate_of:
            return
        # те же проверки, что у кнопки «Опубликовать», плюс ни одного замечания; run_sync — ленивые связи новой работы
        errs, issues = await uow.session.run_sync(lambda _: check_work(w))
        if not errs and not issues:
            w.status, w.published_at, item.status = "published", now(), "published"
            logger.info(
                "Опубликовано без модератора", extra={"audit": True, "work_id": str(w.id), "item_id": str(item.id)}
            )

    async def _fields(
        self, uow: UnitOfWork, item: ImportItem, ai: dict[str, Any], ai_meta: dict, work_type: str
    ) -> tuple[dict[str, Any], dict]:
        """Поля черновика: ответ ИИ, поверх — заполненные ячейки таблицы сведений, она надёжнее ИИ.

        ФИО из таблицы заменяет ФИО от ИИ до того, как под него заведён профиль: иначе профиль остался бы без работ.
        """
        record = item.meta or {}
        author, supervisor = record.get("ФИО автора"), record.get("ФИО руководителя")
        ai_authors = [] if author else ai.get("authors") or []
        ai_supervisors = ai.get("supervisors") or []  # и при руководителе из таблицы: _confirm сверяет ответ ИИ
        found = await self._match(uow, [*ai_authors, *ai_supervisors, *([supervisor] if supervisor else [])])
        meta = {k: dict(v) for k, v in ai_meta.items()}
        await self._confirm(uow, ai, meta, found)
        department = ai.get("department") if meta.get("department", {}).get("confidence") == CONFIRMED else None
        sure = meta.get("authors", {}).get("confidence", 0) >= IN_TEXT  # ФИО есть в тексте — не выдумка модели
        fields: dict[str, Any] = {
            "title": ai.get("title"),
            "annotation": ai.get("annotation"),
            "keywords": ai.get("keywords") or [],
            "year": ai.get("year"),
            "department": ai.get("department"),
            "profile": ai.get("profile"),
            "pages": ai.get("pages"),
            "level": ai.get("level"),
            "language": ai.get("language") or "ru",
            "direction": {"code": ai.get("directionCode"), "name": ai.get("directionName")},
            "authors": [
                await self._student(uow, n, work_type, department) if sure else _ref(n, found.get(n), "student")
                for n in ai_authors
            ],
            "supervisors": [_ref(n, found.get(n), "staff") for n in ai_supervisors],
            "accessLevel": "card_only",
        }
        if work_type == "coursework":
            fields["kind"] = ai.get("courseworkKind") or "work"
        if "directionCode" in meta:
            meta["direction"] = meta.pop("directionCode")
        if record:
            table_fields, table_meta = records.student(record, work_type)
            fields |= table_fields
            meta |= table_meta
            if author:
                fields["authors"] = [await self._student(uow, author, work_type, record.get("Кафедра") or department)]
            if supervisor:
                fields["supervisors"] = [_ref(supervisor, found.get(supervisor), "staff")]
        code = fields["direction"].get("code")
        if code and (d := await uow.session.get(Direction, code)) and d.subjects:
            fields["subjects"] = [dict(zip(("scheme", "code"), s.split(":", 1), strict=True)) for s in d.subjects]
            meta["subjects"] = {"source": "direction"}
        return fields, meta

    async def _student(self, uow: UnitOfWork, full_name: str, work_type: str, department: str | None) -> dict:
        """Автор ВКР или курсовой: найден — ссылка, нет — новый выпускник (ВКР) или студент с этим ФИО."""
        full_name = full_name.strip()
        person = await self.people.match_exact(uow, full_name)
        if person is None:
            person = Person(id=uuid.uuid7(), kind="graduate" if work_type == "vkr" else "student", full_name=full_name)
            person.department = await self.works.known_department(uow, department)
            add_name(person, full_name)
            add_name(person, short_name(full_name))
            uow.session.add(person)
            logger.info("Профиль студента создан", extra={"person_id": str(person.id)})
        return {"personId": str(person.id), "displayName": short_name(person.full_name), "kind": person.kind}

    async def _confirm(self, uow: UnitOfWork, ai: dict[str, Any], meta: dict, found: dict[str, Person]) -> None:
        """Плюс подтвердилось по базе: руководитель среди сотрудников, кафедра в списке, код в справочнике направлений."""

        def bump(field: str) -> None:
            if field in meta and meta[field].get("confidence", 0) >= 0.85:
                meta[field]["confidence"] = CONFIRMED

        supervisors = ai.get("supervisors") or []
        if supervisors and all((p := found.get(n)) and p.kind == "staff" for n in supervisors):
            bump("supervisors")
        if ai.get("department") and await uow.session.get(Department, ai["department"]):
            bump("department")
        if ai.get("directionCode") and (d := await uow.session.get(Direction, ai["directionCode"])):
            bump("directionCode")
            ai.setdefault("directionName", d.name)
