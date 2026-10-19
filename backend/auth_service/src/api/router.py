"""Вход, выход, /auth/me."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from auth_service.src.api.schemas import LoginRequest
from auth_service.src.dependencies import Service
from auth_service.src.service.auth_service import user_dict
from shared.auth.session import SessionUser, current_user
from shared.config import auth_config
from shared.context import get_context

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login")
async def login(body: LoginRequest, request: Request, service: Service) -> JSONResponse:
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    user, token = await service.login(body.login, body.password, ip)
    response = JSONResponse({"user": user_dict(user)})
    # Префикс __Host- запрещает Domain: страница на другом поддомене вуза не подложит свою куку.
    response.set_cookie(
        auth_config.cookie_name,
        token,
        max_age=int(auth_config.ttl.total_seconds()),
        path="/",
        httponly=True,
        secure=auth_config.cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout", status_code=204)
async def logout(service: Service) -> Response:
    if user_id := get_context().user_id:
        await service.logout(UUID(user_id))
    response = Response(status_code=204)
    response.delete_cookie(
        auth_config.cookie_name, path="/", httponly=True, secure=auth_config.cookie_secure, samesite="lax"
    )
    return response


@router.get("/me")
async def me(user: Annotated[SessionUser, Depends(current_user)]) -> dict:
    """Ещё и auth_request nginx: аноним получает 401 до приёма тела загрузки."""
    return {"user": user_dict(user)}
