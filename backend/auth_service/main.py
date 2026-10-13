from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth_service.service import AuthService, user_dict
from shared.auth import AuthMiddleware, OriginCheckMiddleware, SessionUser, current_user
from shared.config import auth_config
from shared.context import get_context
from shared.db.uow import uow_write_factory
from shared.http import create_app

router = APIRouter(prefix="/api/auth")


def get_service() -> AuthService:
    return AuthService(uow_write_factory)


Service = Annotated[AuthService, Depends(get_service)]


class LoginRequest(BaseModel):
    login: str = Field(max_length=100)
    password: str = Field(max_length=200)


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


app = create_app("auth_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware))
