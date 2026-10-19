"""Кабинет автора: свои работы и материалы к ним."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from admin_service.src.dependencies import Author, Authors
from shared.auth.session import require_author

router = APIRouter(prefix="/api/my", dependencies=[Depends(require_author)], tags=["My"])


@router.get("/works")
async def my_works(service: Authors, user: Author) -> dict:
    return await service.works(user)


@router.post("/works/{work_id:uuid}/materials", status_code=201)
async def upload_material(
    service: Authors,
    user: Author,
    work_id: UUID,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["text", "slides", "data", "other"], Form()] = "other",
) -> dict:
    return await service.upload(work_id, file, kind, user)


@router.delete("/works/{work_id:uuid}/materials/{material_id:uuid}", status_code=204)
async def delete_material(service: Authors, user: Author, work_id: UUID, material_id: UUID) -> Response:
    await service.delete(work_id, material_id, user)
    return Response(status_code=204)
