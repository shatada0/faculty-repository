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

from import_service.src.service import records
from import_service.src.service.archive import MAX_FILES, ArchiveError, display_name, extract_pdfs
from shared.auth.session import SessionUser
from shared.bus.consumer import Delivery
from shared.bus.contracts import ImportTopic, ItemCreatedDTO, ItemExtractedDTO
from shared.bus.publisher import MessageBusPublisher
from shared.config import UPLOAD_LIMITS
from shared.db.base import now
from shared.exceptions import BadRequestError, NotFoundError
from shared.imports import ExtractionResult, Import, ImportItem
from shared.imports.tables import TableError, missing_columns, missing_in_row, read_table, to_records
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
                            ImportItem.file_key.is_not(None),
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
        for it in items:  # после коммита, как при загрузке
            await self.publisher.publish(
                ImportTopic.ITEM_CREATED, ItemCreatedDTO(item_id=it.id, file_key=it.file_key, work_type=imp.work_type)
            )
        logger.info("Повтор импорта", extra={"import_id": str(import_id), "files": len(items)})
        return await self.get(import_id, None)

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
        self, uow: UnitOfWork, imp: Import, fields: dict[str, Any], meta: dict, work_type: str = "", **extra: Any
    ) -> tuple[Work, list[str]]:
        """Черновик импорта; дубль ищется по (type, название, год). → (работа, нераспознанные авторы).

        work_type — тип, который воркер узнал по титульнику; без него — тип импорта.
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
        await self.works.refresh_search(uow, [w.id])
        unmatched = list(dict.fromkeys(p.display_name for p in w.people if p.person_id is None and p.display_name))
        return w, unmatched

    async def create(
        self, kind: str, work_type: str, files: list[UploadFile], meta_table: UploadFile | None, user: SessionUser
    ) -> dict:
        if kind == "table" and work_type in ("publication", "talk"):
            if len(files) != 1:
                raise BadRequestError("Таблица импортируется одним файлом")
            import_id = await self._create_table(work_type, files[0], user)
        elif kind == "archive" and work_type in ("vkr", "coursework"):
            import_id = await self._create_archive(work_type, files, meta_table, user)
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
        self, work_type: str, files: list[UploadFile], meta_table: UploadFile | None, user: SessionUser
    ) -> UUID:
        """PDF и zip вперемешку, сколько угодно файлов в одной загрузке: каждый PDF — элемент импорта.

        Тип каждой работы (ВКР или курсовая) воркер узнаёт по титульнику; work_type — на случай, если не узнает.
        """
        if sum(f.size or 0 for f in files) > UPLOAD_LIMITS["archive"]["maxBytes"]:
            raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
        by_file: dict[str, dict] = {}
        if meta_table is not None and meta_table.size:
            for record in to_records(await _table(meta_table), work_type):
                if record.get("Имя файла"):
                    by_file[record["Имя файла"].strip().lower()] = record
        import_id = uuid.uuid7()
        items = []
        with tempfile.TemporaryDirectory() as tmp:
            pdfs: list[tuple[str, Path]] = []
            for k, f in enumerate(files):
                _check_file(f, "archive")
                if not (f.filename or "").lower().endswith(".zip"):
                    # .pdf берём как есть: не PDF внутри — воркер отметит элемент ошибкой, как и файл из zip
                    path = Path(tmp) / f"upload-{k}.pdf"
                    await asyncio.to_thread(_copy, f.file, path)
                    pdfs.append((display_name(f.filename or "file.pdf"), path))
                    continue
                if detect_type(await f.read(8)) != "zip":
                    raise BadRequestError("Файл не подходит по формату или размеру", code="bad_file")
                await f.seek(0)
                await self.storage.put(f"imports/{import_id}/{k}.zip", f.file, "application/zip")
                folder = Path(tmp) / f"zip-{k}"
                folder.mkdir()
                await f.seek(0)
                await asyncio.to_thread(_copy, f.file, folder / "archive.zip")
                try:
                    pdfs += await asyncio.to_thread(extract_pdfs, folder / "archive.zip", folder)
                except ArchiveError as e:
                    raise BadRequestError(f"{f.filename}: {e}", code="bad_file") from e
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
        names = [f.filename or "file" for f in files]
        async with self.uow_factory() as uow:
            uow.session.add(
                Import(
                    id=import_id,
                    kind="archive",
                    work_type=work_type,
                    file_name=names[0] if len(names) == 1 else f"{names[0]} и ещё {len(names) - 1}",
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
        logger.info("Архив принят", extra={"import_id": str(import_id), "uploads": len(files), "files": len(items)})
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
        """Черновик из ответа воркера; без замечаний — сразу опубликованная карточка (текст закрыт до согласия)."""
        work_type = ai.get("workType") if ai.get("workType") in ("vkr", "coursework") else imp.work_type
        meta = {k: dict(v) for k, v in ai_meta.items()}
        await self._confirm(uow, ai, meta)
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
                await (self._student(uow, n, work_type, department) if sure else self._ref(uow, n, "student"))
                for n in ai.get("authors") or []
            ],
            "supervisors": [await self._ref(uow, n, "staff") for n in ai.get("supervisors") or []],
            "accessLevel": "card_only",
        }
        if work_type == "coursework":
            fields["kind"] = ai.get("courseworkKind") or "work"
        if "directionCode" in meta:
            meta["direction"] = meta.pop("directionCode")
        record = item.meta
        if record and not missing_in_row(record, work_type):
            # таблица сведений надёжнее ИИ: её значения поверх
            table_fields, table_meta = records.student(record, work_type)
            fields |= table_fields
            fields["authors"] = [await self._student(uow, record["ФИО автора"], work_type, record.get("Кафедра"))]
            fields["supervisors"] = [await self._ref(uow, record["ФИО руководителя"], "staff")]
            meta |= table_meta
        code = fields["direction"].get("code")
        if code and (d := await uow.session.get(Direction, code)) and d.subjects:
            fields["subjects"] = [dict(zip(("scheme", "code"), s.split(":", 1), strict=True)) for s in d.subjects]
            meta["subjects"] = {"source": "direction"}
        work_id = uuid.uuid7()
        file_key = f"works/{work_id}/{uuid.uuid4()}.pdf"
        await self.storage.copy(item.file_key, file_key)  # imports/ удалит lifecycle через 30 дней
        try:
            async with uow.session.begin_nested():
                w, unmatched = await self._draft(
                    uow, imp, fields, meta, work_type, id=work_id, file_key=file_key, original_file_name=item.ref
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
        if w.duplicate_of:
            return
        # те же проверки, что у кнопки «Опубликовать», плюс ни одного замечания; run_sync — ленивые связи новой работы
        errs, issues = await uow.session.run_sync(lambda _: check_work(w))
        if not errs and not issues:
            w.status, w.published_at, item.status = "published", now(), "published"
            logger.info(
                "Опубликовано без модератора", extra={"audit": True, "work_id": str(w.id), "item_id": str(item.id)}
            )

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
