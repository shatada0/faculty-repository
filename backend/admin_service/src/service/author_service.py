"""Кабинет автора: свои работы и материалы к ним. Работа ищется сразу с условием «я среди авторов» — чужая = 404."""

import uuid
from dataclasses import dataclass
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import ColumnElement, select

from admin_service.src.service.works_service import check_upload
from shared.auth.session import SessionUser
from shared.exceptions import BadRequestError, ConflictError, NotFoundError
from shared.logging import get_logger
from shared.repository.works_repository import with_uploaders
from shared.storage import Storage
from shared.uow import UnitOfWork, UnitOfWorkFactory
from shared.works import Material, Work, WorkPerson
from shared.works import labels as L
from shared.works.schemas import material_dict

logger = get_logger(__name__)


def mine(person_id: UUID) -> ColumnElement[bool]:
    """Автор — среди авторов или докладчиков."""
    return Work.id.in_(
        select(WorkPerson.work_id).where(
            WorkPerson.person_id == person_id, WorkPerson.role.in_(("author", "presenter"))
        )
    )


@dataclass(slots=True)
class AuthorService:
    uow_factory: UnitOfWorkFactory
    storage: Storage

    async def _work(self, uow: UnitOfWork, work_id: UUID, user: SessionUser) -> Work:
        stmt = select(Work).where(Work.id == work_id, mine(user.person_id))
        w = (await uow.session.execute(stmt)).scalar_one_or_none()
        if w is None:
            raise NotFoundError("Работа не найдена")
        return w

    async def works(self, user: SessionUser) -> dict:
        """Свои работы, кроме отклонённых. Черновики со своим именем автор видит, но не меняет."""
        stmt = (
            select(Work)
            .where(mine(user.person_id), Work.status != "rejected")
            .options(with_uploaders())
            .order_by(Work.year.desc().nulls_last(), Work.title.collate("ru-RU-x-icu"))
        )
        async with self.uow_factory() as uow:
            works = (await uow.session.execute(stmt)).scalars().all()
        return {
            "items": [
                {
                    "id": str(w.id),
                    "title": w.title,
                    "kind": L.work_kind(w.type, w.details.get("kind")),
                    "year": w.year,
                    "public": w.status == "published" and w.access_level != "hidden",
                    "canUpload": w.type in ("publication", "talk"),
                    "materials": [material_dict(m, m.uploader.name if m.uploader else "") for m in w.materials],
                }
                for w in works
            ]
        }

    async def upload(self, work_id: UUID, file: UploadFile, kind: str, user: SessionUser) -> dict:
        async with self.uow_factory() as uow:
            w = await self._work(uow, work_id, user)
            if w.type not in ("publication", "talk"):
                raise BadRequestError("Материалы добавляются к публикациям и выступлениям")
        ext, content_type = await check_upload(file, "material")
        key = f"works/{work_id}/materials/{uuid.uuid4()}"
        await self.storage.put(key, file.file, content_type)
        m = Material(
            id=uuid.uuid7(),
            work_id=work_id,
            name=file.filename or f"material{ext}",
            size=file.size or 0,
            kind=kind,
            status="pending",
            file_key=key,
            uploaded_by=user.id,
        )
        async with self.uow_factory() as uow:
            uow.session.add(m)
        logger.info("Материал загружен", extra={"work_id": str(work_id), "material_id": str(m.id)})
        return material_dict(await self._reload(m.id), user.name)

    async def _reload(self, material_id: UUID) -> Material:
        async with self.uow_factory() as uow:
            return await uow.session.get(Material, material_id)

    async def delete(self, work_id: UUID, material_id: UUID, user: SessionUser) -> None:
        """Только свой материал на проверке; старая версия объекта хранится в MinIO 30 дней."""
        async with self.uow_factory() as uow:
            await self._work(uow, work_id, user)
            m = await uow.session.get(Material, material_id)
            if m is None or m.work_id != work_id:
                raise NotFoundError("Материал не найден")
            if m.status != "pending":
                raise ConflictError("Удалить можно только материал на проверке", code="bad_state")
            await uow.session.delete(m)
            key = m.file_key
        await self.storage.delete(key)
        logger.info("Материал удалён автором", extra={"work_id": str(work_id), "material_id": str(material_id)})
