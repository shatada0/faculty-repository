"""Выгрузки модератора: работы (xlsx, csv, zip) и люди."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from admin_service.src.dependencies import Exports, Moderator, Q, WorkType
from shared.auth.session import require_moderator

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)], tags=["Exports"])


@router.get("/exports/works")
async def export_works(
    service: Exports,
    user: Moderator,
    type: WorkType | None = None,
    year: int | None = None,
    status: Literal["draft", "published", "rejected"] | None = None,
    importId: UUID | None = None,
    department: Annotated[str | None, Query(max_length=300)] = None,
    q: Q = None,
    format: Literal["xlsx", "csv"] = "xlsx",
) -> StreamingResponse:
    filters = {"type_": type, "year": year, "status": status, "import_id": importId, "department": department, "q": q}
    return await service.works_table(filters, format, user)


@router.get("/exports/works.zip")
async def export_works_zip(
    service: Exports,
    user: Moderator,
    type: WorkType | None = None,
    year: int | None = None,
    status: Literal["draft", "published", "rejected"] | None = None,
    importId: UUID | None = None,
    department: Annotated[str | None, Query(max_length=300)] = None,
    q: Q = None,
) -> StreamingResponse:
    filters = {"type_": type, "year": year, "status": status, "import_id": importId, "department": department, "q": q}
    return await service.works_zip(filters, user)


@router.get("/exports/people")
async def export_people(
    service: Exports, user: Moderator, format: Literal["xlsx", "csv"] = "xlsx"
) -> StreamingResponse:
    return await service.people(format, user)
