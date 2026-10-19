"""Работы: список, редактор, публикация, bulk, очередь проверки, DOI, файлы и материалы."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile

from admin_service.src.api.schemas import BulkRequest, DoiRequest, MaterialDecision, RejectRequest
from admin_service.src.dependencies import JsonObject, Moderator, Q, Works, WorkType
from shared.auth.session import require_moderator
from shared.exceptions import BadRequestError

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)], tags=["Works"])


@router.get("/summary")
async def summary(service: Works) -> dict:
    return await service.summary()


@router.get("/works")
async def works(
    service: Works,
    type: WorkType | None = None,
    status: Literal["draft", "published", "rejected"] | None = None,
    importId: UUID | None = None,
    year: int | None = None,
    q: Q = None,
    sort: Literal["updated", "problems"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    return await service.list(
        sort=sort, page=page, page_size=pageSize, type_=type, status=status, import_id=importId, year=year, q=q
    )


@router.get("/works/template")
async def work_template(service: Works, type: str | None = None) -> dict:
    if type not in ("vkr", "coursework", "publication", "talk"):
        raise BadRequestError("Неизвестный тип работы")
    return service.template(type)


@router.get("/works/next")
async def next_in_queue(
    service: Works, after: UUID | None = None, type: WorkType | None = None, importId: UUID | None = None
) -> dict:
    return await service.next_in_queue(after, type, importId)


@router.get("/works/related-options")
async def related_options(
    service: Works,
    type: WorkType | None = None,
    author: Annotated[list[UUID], Query(max_length=100)] = [],  # noqa: B006
    include: UUID | None = None,
) -> list[dict]:
    return await service.related_options(type, author, include)


@router.post("/works/bulk")
async def bulk(body: BulkRequest, service: Works, user: Moderator) -> dict:
    if body.action == "access" and body.accessLevel is None:
        raise BadRequestError("Укажите уровень доступа")
    return await service.bulk(body.ids, body.action, body.accessLevel, user)


@router.post("/works", status_code=201)
async def create_work(body: JsonObject, service: Works, user: Moderator) -> dict:
    return await service.create(body, user)


@router.get("/works/{work_id:uuid}")
async def get_work(service: Works, work_id: UUID) -> dict:
    return await service.get(work_id)


@router.patch("/works/{work_id:uuid}")
async def patch_work(body: JsonObject, service: Works, work_id: UUID) -> dict:
    return await service.patch(work_id, body)


@router.post("/works/{work_id:uuid}/file")
async def upload_work_file(service: Works, work_id: UUID, file: Annotated[UploadFile, File()]) -> dict:
    return await service.upload_file(work_id, file)


@router.get("/works/{work_id:uuid}/file")
async def work_file(service: Works, work_id: UUID) -> dict:
    return await service.file_link(work_id)


@router.get("/works/{work_id:uuid}/materials/{material_id:uuid}")
async def material_link(service: Works, work_id: UUID, material_id: UUID) -> dict:
    return await service.material_link(work_id, material_id)


@router.post("/works/{work_id:uuid}/materials/{material_id:uuid}")
async def review_material(
    body: MaterialDecision, service: Works, user: Moderator, work_id: UUID, material_id: UUID
) -> dict:
    return await service.review_material(work_id, material_id, body.status, user)


@router.post("/works/{work_id:uuid}/publish")
async def publish(service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.publish(work_id, user)


@router.post("/works/{work_id:uuid}/unpublish")
async def unpublish(service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.unpublish(work_id, user)


@router.post("/works/{work_id:uuid}/reject")
async def reject(body: RejectRequest, service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.reject(work_id, body.reason.strip() or None, user)


@router.post("/works/{work_id:uuid}/doi")
async def fill_from_doi(body: DoiRequest, service: Works, work_id: UUID) -> dict:
    return await service.fill_from_doi(work_id, body.doi, body.overwrite)


@router.get("/events")
async def events(service: Works, q: Annotated[str, Query(max_length=200)] = "") -> list[dict]:
    return await service.events(q)


@router.get("/classifiers/{scheme}")
async def classifiers(
    service: Works, scheme: Literal["grnti", "oecd", "wos", "asjc"], q: Annotated[str, Query(max_length=200)] = ""
) -> list[dict]:
    return await service.classifiers(scheme, q)
