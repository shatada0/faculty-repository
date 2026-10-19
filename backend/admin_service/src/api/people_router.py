"""Люди: список, карточка, правка, сопоставление авторов, слияние."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from admin_service.src.api.schemas import MatchRequest, MergeRequest
from admin_service.src.dependencies import Moderator, People, Q
from admin_service.src.dto import PersonIn
from shared.auth.session import require_moderator

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)], tags=["People"])


@router.get("/people")
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


@router.post("/people", status_code=201)
async def create_person(body: PersonIn, service: People) -> dict:
    return await service.create(body)


@router.post("/people/match")
async def match_author(body: MatchRequest, service: People) -> dict:
    return await service.match(body.displayName, body.personId, body.kind)


@router.get("/people/{person_id:uuid}")
async def person_card(service: People, person_id: UUID) -> dict:
    return await service.card(person_id)


@router.patch("/people/{person_id:uuid}")
async def patch_person(body: PersonIn, service: People, person_id: UUID) -> dict:
    return await service.patch(person_id, body)


@router.post("/people/{person_id:uuid}/merge")
async def merge_people(body: MergeRequest, service: People, user: Moderator, person_id: UUID) -> dict:
    return await service.merge(person_id, body.fromId, user)
