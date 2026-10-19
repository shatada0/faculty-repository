"""Выгрузки модератора: таблица работ (xlsx, csv), zip с PDF, люди. Всё потоком, без фоновых задач.

Колонки — как в шаблоне импорта типа: модераторам не учить второй формат. Обратно таблица не импортируется.
"""

import asyncio
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func, select

from admin_service.src.service.works_service import conditions
from shared.auth.session import SessionUser
from shared.config import app_config
from shared.imports.tables import TEMPLATES, csv_line, xlsx_cell
from shared.logging import get_logger
from shared.people import Person
from shared.storage import Storage, content_disposition
from shared.uow import UnitOfWorkFactory
from shared.works import Work, WorkPerson
from shared.works import labels as L
from shared.works.schemas import work_dict

logger = get_logger(__name__)

COMMON = ["Тип", "Название", "Авторы", "Год", "Кафедра"]
SERVICE = ["id", "Статус", "Уровень доступа", "Ссылка на карточку"]
CHUNK_ROWS = 500


def _names(refs: list[dict]) -> str:
    return "; ".join(a["displayName"] for a in refs)


def _ru_date(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


def _row(d: dict[str, Any], work_type: str | None) -> list[Any]:
    """Работа → строка в колонках шаблона импорта этого типа (или общие колонки, если типы смешаны)."""
    if work_type in ("vkr", "coursework"):
        direction = d["direction"]
        values = {
            "Имя файла": d.get("originalFileName"),
            "ФИО автора": _names(d["authors"]),
            "Тема": d["title"],
            "ФИО руководителя": _names(d["supervisors"]),
            "Степень руководителя": "; ".join(filter(None, d["supervisorDegrees"])),
            "Год": d["year"],
            "Уровень": L.LEVEL.get(d["level"] or ""),
            "Код направления": direction["code"],
            "Название направления": direction["name"],
            "Профиль": d["profile"],
            "Дисциплина": d["discipline"],
            "Работа или проект": L.COURSEWORK_KIND.get(d.get("kind") or ""),
            "Кафедра": d["department"],
            "Согласие на публикацию текста": "да" if d["consent"] else "нет",
        }
    elif work_type == "publication":
        values = {
            "Авторы": _names(d["authors"]),
            "Название": d["title"],
            "Вид": L.PUB_KIND.get(d["kind"] or ""),
            "Журнал/сборник": d["venue"],
            "Год": d["year"],
            "Том": d["volume"],
            "Номер": d["issue"],
            "Страницы": d["pages"],
            "Издательство": d["publisher"],
            "DOI": d["doi"],
            "ISSN/ISBN": d["issn"] or d["isbn"],
            "Индексация": ", ".join(L.INDEXING[i] for i in d["indexing"]),
            "Ссылка": d["externalUrl"],
            "Кафедра": d["department"],
        }
    elif work_type == "talk":
        e = d["event"]
        presenters = {a["displayName"] for a in d["presenters"]}
        values = {
            "Докладчики": _names(d["presenters"]),
            "Соавторы": _names([a for a in d["authors"] if a["displayName"] not in presenters]),
            "Название доклада": d["title"],
            "Конференция": e["name"],
            "Уровень": L.CONF_LEVEL.get(e["level"] or ""),
            "Город": e["city"],
            "Страна": e["country"],
            "Дата начала": _ru_date(e["dateStart"]),
            "Дата окончания": _ru_date(e["dateEnd"]),
            "Вид доклада": L.TALK_KIND.get(d["kind"] or ""),
            "Формат": L.TALK_FORMAT.get(d["format"] or ""),
            "Награда": d["award"],
            "Ссылка": e["url"],
            "Кафедра": d["department"],
        }
    else:
        values = {
            "Тип": L.WORK_TYPE[d["type"]],
            "Название": d["title"],
            "Авторы": _names(d["authors"]),
            "Год": d["year"],
            "Кафедра": d["department"],
        }
    columns = [name for name, _ in TEMPLATES[work_type]] if work_type else COMMON
    return [
        *(values.get(c) for c in columns),
        d["id"],
        L.STATUS[d["status"]],
        L.ACCESS[d["accessLevel"]],
        f"https://{app_config.site_domain}/admin/works/{d['id']}",
    ]


def header(work_type: str | None) -> list[str]:
    return [*([name for name, _ in TEMPLATES[work_type]] if work_type else COMMON), *SERVICE]


class _Pipe:
    """Неперематываемый поток для zipfile: записанное забирается кусками и сразу уходит клиенту."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.size = 0

    def write(self, data: bytes) -> int:
        self.chunks.append(bytes(data))
        self.size += len(data)
        return len(data)

    def tell(self) -> int:
        return self.size

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data, self.chunks = b"".join(self.chunks), []
        return data


def attachment(body: AsyncIterator[bytes] | Iterator[bytes], filename: str, media_type: str) -> StreamingResponse:
    return StreamingResponse(
        body, media_type=media_type, headers={"Content-Disposition": content_disposition("attachment", filename)}
    )


@dataclass(slots=True)
class ExportService:
    uow_factory: UnitOfWorkFactory
    storage: Storage

    async def _works(self, filters: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Работы по фильтрам «Всех работ» пачками: память не растёт с числом работ."""
        where = conditions(**filters)
        async with self.uow_factory() as uow:
            # степень руководителя — из карточки человека; сотрудников сотни, словарь на всю выгрузку
            degrees = dict(
                (
                    await uow.session.execute(
                        select(Person.id, Person.academic_degree).where(Person.academic_degree.is_not(None))
                    )
                ).all()
            )
        last = None
        while True:
            stmt = select(Work).where(*where).order_by(Work.id).limit(CHUNK_ROWS)
            if last:
                stmt = stmt.where(Work.id > last)
            async with self.uow_factory() as uow:
                batch = (await uow.session.execute(stmt)).scalars().all()
            for w in batch:
                yield work_dict(w) | {
                    "supervisorDegrees": [degrees.get(p.person_id) for p in w.refs("supervisor")],
                    "status": w.status,
                    "originalFileName": w.original_file_name,
                    "fileKey": w.file_key,
                }
            if len(batch) < CHUNK_ROWS:
                return
            last = batch[-1].id

    async def _xlsx(self, rows: AsyncIterator[list[Any]], head: list[str]) -> tuple[Path, int]:
        """openpyxl write_only: строки пишутся по одной во временный файл; сохранение — в потоке, не в цикле событий."""
        wb = Workbook(write_only=True)
        ws = wb.create_sheet()
        ws.append(head)
        count = 0
        async for row in rows:
            ws.append([xlsx_cell(ws, v) for v in row])
            count += 1
        fd, name = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)  # openpyxl открывает файл по пути сам; незакрытый fd утекал бы на каждой выгрузке
        path = Path(name)
        await asyncio.to_thread(wb.save, path)
        return path, count

    @staticmethod
    async def _file(path: Path) -> AsyncIterator[bytes]:
        try:
            with path.open("rb") as f:
                while chunk := await asyncio.to_thread(f.read, 1024 * 1024):
                    yield chunk
        finally:
            await asyncio.to_thread(path.unlink, missing_ok=True)

    def _audit(self, user: SessionUser, what: str, filters: dict[str, Any], rows: int, files: int = 0) -> None:
        # В выгрузках персональные данные студентов: каждая выгрузка — в лог.
        logger.info(
            "Выгрузка",
            extra={
                "audit": True,
                "what": what,
                "by": str(user.id),
                "filters": {k: str(v) for k, v in filters.items() if v},
                "rows": rows,
                "files": files,
            },
        )

    async def works_table(self, filters: dict[str, Any], fmt: str, user: SessionUser) -> StreamingResponse:
        work_type = filters.get("type_")
        name = f"работы-{date.today().isoformat()}.{fmt}"
        if fmt == "xlsx":
            path, count = await self._xlsx((_row(d, work_type) async for d in self._works(filters)), header(work_type))
            self._audit(user, "works.xlsx", filters, count)
            return attachment(
                self._file(path), name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        async def csv_body() -> AsyncIterator[bytes]:
            # utf-8-sig и «;»: без BOM русский Excel покажет кракозябры, запятую он разделителем не считает
            count = 0
            try:
                yield csv_line(header(work_type)).encode("utf-8-sig")
                async for d in self._works(filters):
                    yield csv_line(_row(d, work_type)).encode("utf-8")
                    count += 1
            finally:  # оборванная загрузка — тоже выгрузка: в лог с тем, что успели отдать
                self._audit(user, "works.csv", filters, count)

        return attachment(csv_body(), name, "text/csv; charset=utf-8")

    async def works_zip(self, filters: dict[str, Any], user: SessionUser) -> StreamingResponse:
        """works.xlsx и файлы в files/{id}.pdf. PDF уже сжаты — ZIP_STORED; из MinIO читаются кусками."""
        work_type = filters.get("type_")
        keys: list[tuple[str, str]] = []

        async def rows() -> AsyncIterator[list[Any]]:
            async for d in self._works(filters):
                if d["fileKey"]:
                    keys.append((d["id"], d["fileKey"]))
                yield _row(d, work_type)

        async def body() -> AsyncIterator[bytes]:
            count = files = 0
            try:
                path, count = await self._xlsx(rows(), header(work_type))
                pipe = _Pipe()
                with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_STORED) as zf:
                    await asyncio.to_thread(zf.write, path, "works.xlsx")
                    path.unlink(missing_ok=True)
                    yield pipe.take()
                    for work_id, key in keys:
                        # Первый кусок — до записи в архив: пропавший объект иначе оборвал бы zip на полпути
                        chunks = self.storage.stream(key)
                        try:
                            first = await anext(chunks, b"")
                        except ClientError as e:
                            logger.warning(
                                "Файла нет в хранилище", extra={"work_id": work_id, "key": key, "error": repr(e)}
                            )
                            continue
                        ext = key[key.rfind(".") :] if "." in key.rsplit("/", 1)[-1] else ".pdf"
                        info = zipfile.ZipInfo(f"files/{work_id}{ext}", date.today().timetuple()[:6])
                        with zf.open(info, "w", force_zip64=True) as out:
                            out.write(first)
                            async for chunk in chunks:
                                out.write(chunk)
                                yield pipe.take()
                        files += 1
                yield pipe.take()
            finally:  # как у csv: оборванный архив тоже в лог, с числом отданных файлов
                self._audit(user, "works.zip", filters, count, files)

        return attachment(body(), f"работы-{date.today().isoformat()}.zip", "application/zip")

    async def people(self, fmt: str, user: SessionUser) -> StreamingResponse:
        head = ["ФИО", "Варианты написания", "Вид", "Кафедра", "Должность", "ORCID", "Число работ"]
        async with self.uow_factory() as uow:
            people = (
                (
                    await uow.session.execute(
                        select(Person)
                        .where(Person.merged_into.is_(None))
                        .order_by(Person.full_name.collate("ru-RU-x-icu"))
                    )
                )
                .scalars()
                .all()
            )
            counts = dict(
                (
                    await uow.session.execute(
                        select(WorkPerson.person_id, func.count(WorkPerson.work_id.distinct())).group_by(
                            WorkPerson.person_id
                        )
                    )
                ).all()
            )
        rows = [
            [
                p.full_name,
                "; ".join(p.aliases),
                L.PERSON_KIND[p.kind],
                p.department,
                p.position,
                p.orcid,
                counts.get(p.id, 0),
            ]
            for p in people
        ]
        self._audit(user, f"people.{fmt}", {}, len(rows))
        name = f"люди-{date.today().isoformat()}.{fmt}"
        if fmt == "csv":
            lines = [csv_line(head).encode("utf-8-sig"), *(csv_line(r).encode("utf-8") for r in rows)]
            return attachment(iter(lines), name, "text/csv; charset=utf-8")

        async def gen() -> AsyncIterator[list[Any]]:
            for r in rows:
                yield r

        path, _ = await self._xlsx(gen(), head)
        return attachment(self._file(path), name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
