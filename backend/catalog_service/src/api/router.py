"""Публичный REST каталога. Только чтение: uow_ro_factory, несколько реплик можно запускать."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from catalog_service.src.api.schemas import ClientError, Suggest
from catalog_service.src.dependencies import Filters, Q, Service, WorkType
from shared.config import UPLOAD_LIMITS
from shared.logging import get_logger

router = APIRouter(prefix="/api", tags=["Catalog"])
logger = get_logger("client")


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


@router.get("/works/suggest", response_model_exclude_none=True)
async def suggest(service: Service, q: Q = None) -> Suggest:
    """Живой поиск: работы находятся по началам слов и с опечатками, как и в /works."""
    return await service.suggest(q or "")


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


@router.post("/client-errors", status_code=204)
async def client_errors(body: ClientError) -> Response:
    logger.error("Ошибка в браузере", extra={"client": body.model_dump()})
    return Response(status_code=204)
