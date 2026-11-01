"""Тела запросов рабочего места и кабинета автора."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BulkRequest(BaseModel):
    ids: list[UUID] = Field(max_length=500)
    action: Literal["publish", "access"]
    accessLevel: Literal["full", "card_only", "hidden"] | None = None


class MaterialDecision(BaseModel):
    status: Literal["approved", "rejected"]
    reason: str = Field("", max_length=2000)  # автору видно, почему отклонили


class RejectRequest(BaseModel):
    reason: str = Field("", max_length=2000)


class DoiRequest(BaseModel):
    doi: str = Field("", max_length=300)
    overwrite: list[str] = Field(default_factory=list, max_length=20)


class IsbnRequest(BaseModel):
    isbn: str = Field("", max_length=40)
    overwrite: list[str] = Field(default_factory=list, max_length=20)


class DoiListRequest(BaseModel):
    text: str = Field(max_length=200_000)  # DOI через пробелы, запятые или строками, ссылками doi.org


class MatchRequest(BaseModel):
    displayName: str = Field(max_length=300)
    personId: UUID | None = None
    kind: Literal["staff", "student", "graduate", "external"] = "external"


class MergeRequest(BaseModel):
    fromId: UUID
