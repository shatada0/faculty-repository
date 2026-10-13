"""Публичный REST каталога. Только чтение: uow_ro_factory, несколько реплик можно запускать."""

from collections import defaultdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from catalog_service.service import CatalogService, Params
from shared.config import UPLOAD_LIMITS
from shared.db.uow import uow_ro_factory
from shared.logging import get_logger
from shared.repository.people import PersonRepository
from shared.repository.works import WorkRepository

router = APIRouter(prefix="/api")
logger = get_logger("client")

WorkType = Literal["vkr", "coursework", "publication", "talk"]
Q = Annotated[str | None, Query(max_length=200)]


def get_service(request: Request) -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), request.app.state.storage)


def filter_params(request: Request) -> Params:
    """Фильтры фасетов — любые повторяющиеся ключи запроса; неизвестные ключи никуда не попадают."""
    out: Params = defaultdict(list)
    for k, v in request.query_params.multi_items():
        out[k].append(v[:200])
    return out


Service = Annotated[CatalogService, Depends(get_service)]
Filters = Annotated[Params, Depends(filter_params)]


@router.get("/stats")
async def stats(service: Service) -> dict:
    return await service.stats()


@router.get("/works")
async def works(
    service: Service,
    filters: Filters,
    q: Q = None,
    type: WorkType | None = None,
    exclude: UUID | None = None,
    sort: Literal["relevance", "new", "title"] | None = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return await service.catalog(filters, q=q, type_=type, exclude=exclude, sort=sort, page=page, page_size=pageSize)


@router.get("/works/latest")
async def latest(service: Service) -> list[dict]:
    return await service.latest()


@router.get("/works/facets")
async def facets(service: Service, filters: Filters, q: Q = None, type: WorkType | None = None) -> dict:
    return await service.facets(filters, q=q, type_=type)


@router.get("/works/{work_id:uuid}")
async def work(service: Service, work_id: UUID) -> dict:
    return await service.work(work_id)


@router.get("/works/{work_id:uuid}/file")
async def work_file(service: Service, work_id: UUID) -> dict:
    return await service.file(work_id)


@router.get("/works/{work_id:uuid}/citation")
async def citation(service: Service, work_id: UUID) -> dict:
    return await service.citation(work_id)


@router.get("/works/{work_id:uuid}/materials/{material_id:uuid}")
async def material(service: Service, work_id: UUID, material_id: UUID) -> dict:
    return await service.material(work_id, material_id)


@router.get("/departments")
async def departments(service: Service) -> dict:
    return await service.departments()


@router.get("/people")
async def people(
    service: Service,
    q: Q = None,
    department: Annotated[list[str], Query()] = [],  # noqa: B006
    sort: Literal["works", "name"] | None = None,
) -> dict:
    return await service.staff(q=q, departments=department, sort=sort)


@router.get("/people/{person_id:uuid}")
async def person(service: Service, person_id: UUID) -> dict:
    return await service.profile(person_id)


@router.get("/people/{person_id:uuid}/works")
async def person_works(
    service: Service,
    filters: Filters,
    person_id: UUID,
    type: WorkType | None = None,
    role: Literal["author", "supervisor"] = "author",
) -> dict:
    return await service.person_works(person_id, filters, type_=type, role=role)


@router.get("/people/{person_id:uuid}/metrics")
async def person_metrics(
    service: Service, person_id: UUID, year: Annotated[str | None, Query(max_length=4)] = None
) -> dict:
    return await service.metrics(person_id, year)


@router.get("/upload-limits")
async def upload_limits() -> dict:
    return UPLOAD_LIMITS


class ClientError(BaseModel):
    """Ошибка браузера: в лог со всем, что поможет найти её по trace_id запроса."""

    model_config = ConfigDict(extra="ignore")
    message: str = Field("", max_length=2000)
    stack: str | None = Field(None, max_length=10000)
    url: str | None = Field(None, max_length=2000)
    userAgent: str | None = Field(None, max_length=500)
    build: str | None = Field(None, max_length=100)
    traceId: str | None = Field(None, max_length=100)


@router.post("/client-errors", status_code=204)
async def client_errors(body: ClientError) -> Response:
    logger.error("Ошибка в браузере", extra={"client": body.model_dump()})
    return Response(status_code=204)
