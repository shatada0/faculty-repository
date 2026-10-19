"""Сборка сервиса импорта: и для ручек, и для потребителя шины."""

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request

from import_service.src.service.import_service import ImportService
from shared.auth.session import SessionUser, require_moderator
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_write_factory

WorkType = Literal["vkr", "coursework", "publication", "talk"]
Moderator = Annotated[SessionUser, Depends(require_moderator)]


def build_service(app: FastAPI) -> ImportService:
    return ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), app.state.storage, app.state.publisher
    )


def get_service(request: Request) -> ImportService:
    return build_service(request.app)


Service = Annotated[ImportService, Depends(get_service)]
