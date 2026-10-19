"""Сборка сервисов и псевдонимы зависимостей: права проверяет require_* на роутере, сервис получает готового пользователя."""

from typing import Annotated, Any, Literal

from fastapi import Body, Depends, Query, Request

from admin_service.src.service.author_service import AuthorService
from admin_service.src.service.export_service import ExportService
from admin_service.src.service.people_service import PeopleService
from admin_service.src.service.works_service import WorkService
from shared.auth.session import SessionUser, require_author, require_moderator
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_write_factory

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
