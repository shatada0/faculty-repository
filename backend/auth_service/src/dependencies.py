"""Сборка сервиса входа."""

from typing import Annotated

from fastapi import Depends

from auth_service.src.service.auth_service import AuthService
from shared.uow import uow_write_factory


def get_service() -> AuthService:
    return AuthService(uow_write_factory)


Service = Annotated[AuthService, Depends(get_service)]
