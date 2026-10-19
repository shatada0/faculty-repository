"""Импорт: загрузка, предпросмотр таблицы, список и детали, шаблоны таблиц."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from import_service.src.dependencies import Moderator, Service, WorkType
from shared.auth.session import require_moderator
from shared.exceptions import NotFoundError
from shared.imports.tables import TEMPLATES, template_csv
from shared.storage import content_disposition

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)], tags=["Imports"])


@router.post("/imports/preview")
async def preview(service: Service, workType: Annotated[WorkType, Form()], file: Annotated[UploadFile, File()]) -> dict:
    return await service.preview(workType, file)


@router.post("/imports", status_code=201)
async def create_import(
    service: Service,
    user: Moderator,
    kind: Annotated[Literal["archive", "table"], Form()],
    workType: Annotated[WorkType, Form()],
    file: Annotated[list[UploadFile], File()],  # архив: PDF и zip вперемешку, поле file повторяется
    metaTable: Annotated[UploadFile | None, File()] = None,
) -> dict:
    return await service.create(kind, workType, file, metaTable, user)


@router.get("/imports")
async def imports(service: Service) -> list[dict]:
    return await service.list()


@router.get("/imports/{import_id:uuid}")
async def import_detail(
    service: Service,
    import_id: UUID,
    status: Literal["pending", "extracted", "filled", "duplicate", "error"] | None = None,
) -> dict:
    return await service.get(import_id, status)


@router.post("/imports/{import_id:uuid}/retry")
async def retry_import(service: Service, import_id: UUID) -> dict:
    return await service.retry(import_id)


@router.get("/templates/{work_type}/columns")
async def template_columns(work_type: str) -> list[dict]:
    if work_type not in TEMPLATES:
        raise NotFoundError("Нет такого шаблона")
    return [{"name": name, "required": required} for name, required in TEMPLATES[work_type]]


@router.get("/templates/{work_type}")
async def template(work_type: str) -> Response:
    if work_type not in TEMPLATES:
        raise NotFoundError("Нет такого шаблона")
    return Response(
        template_csv(work_type),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition("attachment", f"шаблон-{work_type}.csv")},
    )
