"""REST рабочего места: /api/admin/* — модератор, /api/my/* — автор. Права — зависимостью на роутере."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from admin_service.exports import ExportService
from admin_service.my import AuthorService
from admin_service.people import PeopleService, PersonIn
from admin_service.service import WorkService
from shared.auth import SessionUser, require_author, require_moderator
from shared.db.uow import uow_write_factory
from shared.exceptions import BadRequestError
from shared.repository.people import PersonRepository
from shared.repository.works import WorkRepository

admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)])
my = APIRouter(prefix="/api/my", dependencies=[Depends(require_author)])

WorkType = Literal["vkr", "coursework", "publication", "talk"]
Q = Annotated[str | None, Query(max_length=200)]
Moderator = Annotated[SessionUser, Depends(require_moderator)]
Author = Annotated[SessionUser, Depends(require_author)]


def works_service(request: Request) -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), request.app.state.storage)


def people_service() -> PeopleService:
    return PeopleService(uow_write_factory, PersonRepository(), WorkRepository())


def author_service(request: Request) -> AuthorService:
    return AuthorService(uow_write_factory, request.app.state.storage)


def export_service(request: Request) -> ExportService:
    return ExportService(uow_write_factory, request.app.state.storage)


Works = Annotated[WorkService, Depends(works_service)]
People = Annotated[PeopleService, Depends(people_service)]
Authors = Annotated[AuthorService, Depends(author_service)]
Exports = Annotated[ExportService, Depends(export_service)]
JsonObject = Annotated[dict[str, Any], Body()]


# ---------- работы ----------


@admin.get("/summary")
async def summary(service: Works) -> dict:
    return await service.summary()


@admin.get("/works")
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


@admin.get("/works/template")
async def work_template(service: Works, type: str | None = None) -> dict:
    if type not in ("vkr", "coursework", "publication", "talk"):
        raise BadRequestError("Неизвестный тип работы")
    return service.template(type)


@admin.get("/works/next")
async def next_in_queue(
    service: Works, after: UUID | None = None, type: WorkType | None = None, importId: UUID | None = None
) -> dict:
    return await service.next_in_queue(after, type, importId)


@admin.get("/works/related-options")
async def related_options(
    service: Works,
    type: WorkType | None = None,
    author: Annotated[list[UUID], Query(max_length=100)] = [],  # noqa: B006
    include: UUID | None = None,
) -> list[dict]:
    return await service.related_options(type, author, include)


class BulkRequest(BaseModel):
    ids: list[UUID] = Field(max_length=500)
    action: Literal["publish", "access"]
    accessLevel: Literal["full", "card_only", "hidden"] | None = None


@admin.post("/works/bulk")
async def bulk(body: BulkRequest, service: Works, user: Moderator) -> dict:
    if body.action == "access" and body.accessLevel is None:
        raise BadRequestError("Укажите уровень доступа")
    return await service.bulk(body.ids, body.action, body.accessLevel, user)


@admin.post("/works", status_code=201)
async def create_work(body: JsonObject, service: Works, user: Moderator) -> dict:
    return await service.create(body, user)


@admin.get("/works/{work_id:uuid}")
async def get_work(service: Works, work_id: UUID) -> dict:
    return await service.get(work_id)


@admin.patch("/works/{work_id:uuid}")
async def patch_work(body: JsonObject, service: Works, work_id: UUID) -> dict:
    return await service.patch(work_id, body)


@admin.post("/works/{work_id:uuid}/file")
async def upload_work_file(service: Works, work_id: UUID, file: Annotated[UploadFile, File()]) -> dict:
    return await service.upload_file(work_id, file)


@admin.get("/works/{work_id:uuid}/file")
async def work_file(service: Works, work_id: UUID) -> dict:
    return await service.file_link(work_id)


@admin.get("/works/{work_id:uuid}/materials/{material_id:uuid}")
async def material_link(service: Works, work_id: UUID, material_id: UUID) -> dict:
    return await service.material_link(work_id, material_id)


class MaterialDecision(BaseModel):
    status: Literal["approved", "rejected"]


@admin.post("/works/{work_id:uuid}/materials/{material_id:uuid}")
async def review_material(
    body: MaterialDecision, service: Works, user: Moderator, work_id: UUID, material_id: UUID
) -> dict:
    return await service.review_material(work_id, material_id, body.status, user)


@admin.post("/works/{work_id:uuid}/publish")
async def publish(service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.publish(work_id, user)


@admin.post("/works/{work_id:uuid}/unpublish")
async def unpublish(service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.unpublish(work_id, user)


class RejectRequest(BaseModel):
    reason: str = Field("", max_length=2000)


@admin.post("/works/{work_id:uuid}/reject")
async def reject(body: RejectRequest, service: Works, user: Moderator, work_id: UUID) -> dict:
    return await service.reject(work_id, body.reason.strip() or None, user)


class DoiRequest(BaseModel):
    doi: str = Field("", max_length=300)
    overwrite: list[str] = Field(default_factory=list, max_length=20)


@admin.post("/works/{work_id:uuid}/doi")
async def fill_from_doi(body: DoiRequest, service: Works, work_id: UUID) -> dict:
    return await service.fill_from_doi(work_id, body.doi, body.overwrite)


@admin.get("/events")
async def events(service: Works, q: Annotated[str, Query(max_length=200)] = "") -> list[dict]:
    return await service.events(q)


@admin.get("/classifiers/{scheme}")
async def classifiers(
    service: Works, scheme: Literal["grnti", "oecd", "wos", "asjc"], q: Annotated[str, Query(max_length=200)] = ""
) -> list[dict]:
    return await service.classifiers(scheme, q)


# ---------- люди ----------


@admin.get("/people")
async def people(
    service: People,
    q: Q = None,
    kind: Literal["staff", "student", "graduate", "external"] | None = None,
    department: Annotated[str | None, Query(max_length=300)] = None,
    exclude: UUID | None = None,
    unmatched: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    if unmatched:
        return await service.unmatched()
    return await service.list(q=q, kind=kind, department=department, exclude=exclude, page=page, page_size=pageSize)


@admin.post("/people", status_code=201)
async def create_person(body: PersonIn, service: People) -> dict:
    return await service.create(body)


class MatchRequest(BaseModel):
    displayName: str = Field(max_length=300)
    personId: UUID | None = None
    kind: Literal["staff", "student", "graduate", "external"] = "external"


@admin.post("/people/match")
async def match_author(body: MatchRequest, service: People) -> dict:
    return await service.match(body.displayName, body.personId, body.kind)


@admin.get("/people/{person_id:uuid}")
async def person_card(service: People, person_id: UUID) -> dict:
    return await service.card(person_id)


@admin.patch("/people/{person_id:uuid}")
async def patch_person(body: PersonIn, service: People, person_id: UUID) -> dict:
    return await service.patch(person_id, body)


class MergeRequest(BaseModel):
    fromId: UUID


@admin.post("/people/{person_id:uuid}/merge")
async def merge_people(body: MergeRequest, service: People, user: Moderator, person_id: UUID) -> dict:
    return await service.merge(person_id, body.fromId, user)


# ---------- выгрузки ----------


@admin.get("/exports/works")
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


@admin.get("/exports/works.zip")
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


@admin.get("/exports/people")
async def export_people(
    service: Exports, user: Moderator, format: Literal["xlsx", "csv"] = "xlsx"
) -> StreamingResponse:
    return await service.people(format, user)


# ---------- кабинет автора ----------


@my.get("/works")
async def my_works(service: Authors, user: Author) -> dict:
    return await service.works(user)


@my.post("/works/{work_id:uuid}/materials", status_code=201)
async def upload_material(
    service: Authors,
    user: Author,
    work_id: UUID,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["text", "slides", "data", "other"], Form()] = "other",
) -> dict:
    return await service.upload(work_id, file, kind, user)


@my.delete("/works/{work_id:uuid}/materials/{material_id:uuid}", status_code=204)
async def delete_material(service: Authors, user: Author, work_id: UUID, material_id: UUID) -> Response:
    await service.delete(work_id, material_id, user)
    return Response(status_code=204)
