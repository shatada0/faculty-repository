"""Люди: список, карточка, правка, сопоставление авторов, слияние."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from admin_service.src.api.schemas import ExternalRequest, MatchRequest, MergeRequest
from admin_service.src.dependencies import Moderator, People, Q, Works
from admin_service.src.dto import PersonIn
from shared.auth.session import require_moderator

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)], tags=["People"])


@router.get("/people")
async def people(
    service: People,
    user: Moderator,
    q: Q = None,
    kind: Literal["staff", "student", "graduate", "external"] | None = None,
    department: Annotated[str | None, Query(max_length=300)] = None,
    exclude: UUID | None = None,
    unmatched: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    if unmatched:
        return await service.unmatched(user)
    return await service.list(q=q, kind=kind, department=department, exclude=exclude, page=page, page_size=pageSize)


@router.post("/people", status_code=201)
async def create_person(body: PersonIn, service: People, user: Moderator) -> dict:
    return await service.create(body, user)


@router.post("/people/match")
async def match_author(body: MatchRequest, service: People, user: Moderator) -> dict:
    return await service.match(body.displayName, body.personId, body.kind, user)


@router.post("/people/match-external")
async def match_external(body: ExternalRequest, service: People, user: Moderator) -> dict:
    """«Остальные — внешние авторы»: каждое написание — новый внешний автор, как кнопка «Внешний автор»."""
    return await service.match_external(body.names, user)


@router.get("/people/{person_id:uuid}")
async def person_card(service: People, person_id: UUID) -> dict:
    return await service.card(person_id)


@router.patch("/people/{person_id:uuid}")
async def patch_person(body: PersonIn, service: People, user: Moderator, person_id: UUID) -> dict:
    return await service.patch(person_id, body, user)


@router.post("/people/{person_id:uuid}/merge")
async def merge_people(body: MergeRequest, service: People, user: Moderator, person_id: UUID) -> dict:
    return await service.merge(person_id, body.fromId, user)


@router.post("/people/{person_id:uuid}/orcid-import")
async def orcid_import(service: Works, user: Moderator, person_id: UUID) -> dict:
    """Работы с DOI из профиля ORCID — черновиками публикаций, заполненными по Crossref."""
    return await service.import_orcid(person_id, user)
