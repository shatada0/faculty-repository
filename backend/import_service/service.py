"""Импорт: таблицы публикаций и докладов — синхронно, архивы ВКР и курсовых — через шину и extraction_worker."""

import asyncio
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select

from import_service import records
from import_service.archive import ArchiveError, extract_pdfs
from shared import labels as L
from shared.auth import SessionUser
from shared.bus.consumer import Delivery
from shared.bus.publisher import MessageBusPublisher
from shared.config import UPLOAD_LIMITS
from shared.contracts import ImportTopic, ItemCreatedDTO, ItemExtractedDTO
from shared.db.uow import UnitOfWork, UnitOfWorkFactory
from shared.exceptions import BadRequestError, NotFoundError
from shared.logging import get_logger
from shared.models import Department, Direction, ExtractionResult, Import, ImportItem, Person, Work
from shared.names import short_name
from shared.repository.people import PersonRepository, add_name
from shared.repository.works import WorkRepository
from shared.storage import Storage, detect_type
from shared.tables import TableError, missing_columns, missing_in_row, read_table, to_records
from shared.works import apply_write, parse_write

logger = get_logger(__name__)

TABLE_ERROR = "Не удалось прочитать таблицу. Проверьте, что это .xlsx или .csv."
CONFIRMED = 0.95  # значение ИИ подтвердилось по базе


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
        count(ImportItem.status == "duplicate").label("duplicates"),
        count(ImportItem.status == "error").label("errors"),
        count(func.cardinality(ImportItem.unmatched_authors) > 0).label("unmatched"),
    )


def _check_file(file: UploadFile, rule: str) -> None:
    limits = UPLOAD_LIMITS[rule]
    name = (file.filename or "").lower()
    if not any(name.endswith(ext) for ext in limits["accept"]) or (file.size or 0) > limits["maxBytes"]:
        raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")


async def _table(file: UploadFile) -> list[list[str]]:
    _check_file(file, "table")
    try:
        return await asyncio.to_thread(read_table, file.filename or "", await file.read())
    except TableError as e:
        raise BadRequestError(TABLE_ERROR, code="bad_table") from e


def _copy(src: IO[bytes], path: Path) -> None:
    with path.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def _bad_value(e: ValidationError) -> str:
    loc = ".".join(str(x) for x in e.errors()[0]["loc"])
    return f"Неверное значение: {records.COLUMN_OF.get(loc, L.FIELD.get(loc, loc))}"


@dataclass(slots=True)
class ImportService:
    uow_factory: UnitOfWorkFactory
    works: WorkRepository
    people: PersonRepository
    storage: Storage
    publisher: MessageBusPublisher | None = None

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
                {k: getattr(r, k) for k in ("total", "processed", "filled", "duplicates", "errors", "unmatched")},
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

    async def preview(self, work_type: str, file: UploadFile) -> dict:
        """Заголовки, первые 10 строк с незаполненными обязательными полями, сколько строк и сколько плохих."""
        rows = await _table(file)
        header, body = (rows[0], rows[1:]) if rows else ([], [])
        missing = [missing_in_row(r, work_type) for r in to_records(rows, work_type)]
        return {
            "header": header,
            "rows": [{"line": i + 2, "cells": cells, "missing": missing[i]} for i, cells in enumerate(body[:10])],
            "total": len(body),
            "bad": sum(1 for m in missing if m),
            "missingColumns": missing_columns(header, work_type),
        }

    # ---------- создание ----------

    async def _ref(self, uow: UnitOfWork, name: str, fallback: str = "external") -> dict:
        person = await self.people.match_exact(uow, name)
        if person:
            return {"personId": str(person.id), "displayName": name, "kind": person.kind}
        return {"personId": None, "displayName": name, "kind": fallback}

    async def _draft(
        self, uow: UnitOfWork, imp: Import, fields: dict[str, Any], meta: dict, **extra: Any
    ) -> tuple[Work, list[str]]:
        """Черновик импорта; дубль ищется по (type, название, год). → (работа, нераспознанные авторы)."""
        model = parse_write(imp.work_type, fields)
        w = Work(
            type=imp.work_type,
            status="draft",
            import_id=imp.id,
            details={},
            keywords=[],
            field_meta=meta,
            **{"id": uuid.uuid7(), **extra},
        )
        apply_write(w, model)
        duplicate = await self.works.find_duplicate(uow, w.type, w.title, w.year)
        w.duplicate_of = duplicate.id if duplicate else None
        await self.works.ensure_department(uow, w.department)
        uow.session.add(w)
        await self.works.refresh_search(uow, [w.id])
        unmatched = list(dict.fromkeys(p.display_name for p in w.people if p.person_id is None and p.display_name))
        return w, unmatched

    async def create(
        self, kind: str, work_type: str, file: UploadFile, meta_table: UploadFile | None, user: SessionUser
    ) -> dict:
        if kind == "table" and work_type in ("publication", "talk"):
            import_id = await self._create_table(work_type, file, user)
        elif kind == "archive" and work_type in ("vkr", "coursework"):
            import_id = await self._create_archive(work_type, file, meta_table, user)
        else:
            raise BadRequestError("Таблицей импортируются публикации и доклады, архивом — ВКР и курсовые")
        return (await self.get(import_id, None))["import"]

    async def _create_table(self, work_type: str, file: UploadFile, user: SessionUser) -> UUID:
        rows = await _table(file)
        if missing := missing_columns(rows[0] if rows else [], work_type):
            raise BadRequestError(
                f"В таблице нет обязательных колонок: {', '.join(missing)}. Скачайте шаблон.", code="bad_table"
            )
        async with self.uow_factory() as uow:
            imp = Import(
                id=uuid.uuid7(),
                kind="table",
                work_type=work_type,
                file_name=file.filename or "table",
                status="done",
                created_by=user.name,
            )
            uow.session.add(imp)
            for n, record in enumerate(to_records(rows, work_type), start=1):
                item = ImportItem(
                    import_id=imp.id,
                    n=n,
                    ref=f"Строка {n + 1}",
                    title=record.get("Название") or record.get("Название доклада") or None,
                )
                uow.session.add(item)
                if miss := missing_in_row(record, work_type):
                    item.status, item.error = "error", records.row_error(miss)
                    continue
                if work_type == "publication":
                    fields, meta, authors = records.publication(record)
                    fields["authors"] = [await self._ref(uow, n) for n in authors]
                else:
                    fields, meta, presenters, coauthors = records.talk(record)
                    fields["presenters"] = [await self._ref(uow, n) for n in presenters]
                    fields["authors"] = [*fields["presenters"], *[await self._ref(uow, n) for n in coauthors]]
                try:
                    async with uow.session.begin_nested():  # строка с ошибкой не роняет весь импорт
                        w, unmatched = await self._draft(uow, imp, fields, meta, created_by=user.id)
                except ValidationError as e:
                    item.status, item.error = "error", _bad_value(e)
                    continue
                item.status = "duplicate" if w.duplicate_of else "filled"
                item.work_id, item.duplicate_of, item.title, item.unmatched_authors = (
                    w.id,
                    w.duplicate_of,
                    w.title,
                    unmatched,
                )
        logger.info("Импорт таблицы", extra={"import_id": str(imp.id), "rows": len(rows) - 1})
        return imp.id

    async def _create_archive(
        self, work_type: str, file: UploadFile, meta_table: UploadFile | None, user: SessionUser
    ) -> UUID:
        _check_file(file, "archive")
        if detect_type(await file.read(8)) != "zip":
            raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
        await file.seek(0)
        by_file: dict[str, dict] = {}
        if meta_table is not None and meta_table.size:
            for record in to_records(await _table(meta_table), work_type):
                if record.get("Имя файла"):
                    by_file[record["Имя файла"].strip().lower()] = record
        import_id = uuid.uuid7()
        await self.storage.put(f"imports/{import_id}/archive.zip", file.file, "application/zip")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive.zip"
            await file.seek(0)
            await asyncio.to_thread(_copy, file.file, archive)
            try:
                pdfs = await asyncio.to_thread(extract_pdfs, archive, Path(tmp))
            except ArchiveError as e:
                raise BadRequestError(str(e), code="bad_file") from e
            items = []
            for n, (name, path) in enumerate(pdfs, start=1):
                key = f"imports/{import_id}/items/{n}.pdf"  # имя из архива в ключ не попадает
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
        async with self.uow_factory() as uow:
            uow.session.add(
                Import(
                    id=import_id,
                    kind="archive",
                    work_type=work_type,
                    file_name=file.filename or "archive.zip",
                    meta_table_name=meta_table.filename if meta_table is not None and meta_table.size else None,
                    status="processing",
                    created_by=user.name,
                )
            )
            await uow.session.flush()
            uow.session.add_all(items)
        # после коммита: воркер не получит элемент, которого ещё нет в базе
        for it in items:
            await self.publisher.publish(
                ImportTopic.ITEM_CREATED, ItemCreatedDTO(item_id=it.id, file_key=it.file_key, work_type=work_type)
            )
        logger.info("Архив принят", extra={"import_id": str(import_id), "files": len(items)})
        return import_id

    # ---------- результат воркера ----------

    async def on_extracted(self, dto: ItemExtractedDTO, _: Delivery) -> None:
        """Черновик из результата ИИ. Повтор сообщения ничего не ломает: обрабатываем только pending."""
        async with self.uow_factory() as uow:
            item = await uow.session.get(ImportItem, dto.item_id)
            if item is None or item.status != "pending":
                logger.info("Элемент уже обработан — пропуск", extra={"item_id": str(dto.item_id)})
                return
            # Блокировка импорта: два элемента одного импорта не решат одновременно, что они последние.
            imp = (
                await uow.session.execute(select(Import).where(Import.id == item.import_id).with_for_update())
            ).scalar_one()
            await uow.session.refresh(item)
            if item.status != "pending":
                return
            if dto.error:
                item.status, item.error = "error", dto.error
            else:
                await self._fill_item(uow, imp, item, dto.fields or {}, dto.field_meta or {})
            await uow.session.flush()
            left = await uow.session.scalar(
                select(func.count()).where(ImportItem.import_id == imp.id, ImportItem.status == "pending")
            )
            if left == 0:
                imp.status = "done"
        logger.info("Элемент импорта готов", extra={"item_id": str(dto.item_id), "status": item.status})

    async def _fill_item(
        self, uow: UnitOfWork, imp: Import, item: ImportItem, ai: dict[str, Any], ai_meta: dict
    ) -> None:
        meta = {k: dict(v) for k, v in ai_meta.items()}
        await self._confirm(uow, ai, meta)
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
            "authors": [await self._ref(uow, n, "student") for n in ai.get("authors") or []],
            "supervisors": [await self._ref(uow, n, "staff") for n in ai.get("supervisors") or []],
            "accessLevel": "card_only",
        }
        if "directionCode" in meta:
            meta["direction"] = meta.pop("directionCode")
        record = item.meta
        if record and not missing_in_row(record, imp.work_type):
            # таблица сведений надёжнее ИИ: её значения поверх
            table_fields, table_meta = records.student(record, imp.work_type)
            fields |= table_fields
            fields["authors"] = [await self._student(uow, record, imp.work_type)]
            fields["supervisors"] = [await self._ref(uow, record["ФИО руководителя"], "staff")]
            meta |= table_meta
        if imp.work_type == "coursework":
            fields.setdefault("kind", "work")
        work_id = uuid.uuid7()
        file_key = f"works/{work_id}/{uuid.uuid4()}.pdf"
        await self.storage.copy(item.file_key, file_key)  # imports/ удалит lifecycle через 30 дней
        try:
            async with uow.session.begin_nested():
                w, unmatched = await self._draft(
                    uow, imp, fields, meta, id=work_id, file_key=file_key, original_file_name=item.ref
                )
        except ValidationError as e:
            item.status, item.error = "error", _bad_value(e)
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
        item.status = "duplicate" if w.duplicate_of else "filled"
        item.work_id, item.duplicate_of, item.title, item.unmatched_authors = w.id, w.duplicate_of, w.title, unmatched

    async def _student(self, uow: UnitOfWork, record: dict[str, str], work_type: str) -> dict:
        """Автор из таблицы сведений: найден — ссылка, нет — новый студент или выпускник с этим ФИО."""
        full_name = record["ФИО автора"].strip()
        person = await self.people.match_exact(uow, full_name)
        if person is None:
            person = Person(id=uuid.uuid7(), kind="graduate" if work_type == "vkr" else "student", full_name=full_name)
            await self.works.ensure_department(uow, record.get("Кафедра"))
            person.department = record.get("Кафедра") or None
            add_name(person, full_name)
            add_name(person, short_name(full_name))
            uow.session.add(person)
        return {"personId": str(person.id), "displayName": short_name(person.full_name), "kind": person.kind}

    async def _confirm(self, uow: UnitOfWork, ai: dict[str, Any], meta: dict) -> None:
        """Плюс подтвердилось по базе: руководитель среди сотрудников, кафедра в списке, код в справочнике направлений."""

        def bump(field: str) -> None:
            if field in meta and meta[field].get("confidence", 0) >= 0.85:
                meta[field]["confidence"] = CONFIRMED

        supervisors = ai.get("supervisors") or []
        staff = [p for n in supervisors if (p := await self.people.match_exact(uow, n)) and p.kind == "staff"]
        if supervisors and len(staff) == len(supervisors):
            bump("supervisors")
        if ai.get("department") and await uow.session.get(Department, ai["department"]):
            bump("department")
        if ai.get("directionCode") and (d := await uow.session.get(Direction, ai["directionCode"])):
            bump("directionCode")
            ai.setdefault("directionName", d.name)
