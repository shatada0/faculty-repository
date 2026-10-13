"""Сессия — JWT (EdDSA) в HttpOnly-куке. Мидлварь только читает токен; права проверяют зависимости роутеров."""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.config import auth_config
from shared.context import MessageContext, get_context
from shared.db.uow import uow_ro_factory
from shared.models import User


@dataclass(frozen=True, slots=True)
class SessionUser:
    """Пользователь сессии. Пароль сюда не попадает: у роли writer нет доступа к password_hash."""

    id: UUID
    name: str
    role: str
    person_id: UUID | None
    is_active: bool
    token_version: int


@cache
def _key(name: str) -> bytes:
    return (auth_config.keys_dir / name).read_bytes()


def issue_token(user: SessionUser) -> str:
    """Только auth_service: у остальных нет приватного ключа."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id),
        "ver": user.token_version,
        "name": user.name,
        "iat": now,
        "exp": now + auth_config.ttl,
    }
    return jwt.encode(claims, _key("jwt_private"), algorithm="EdDSA")


def read_token(token: str) -> dict | None:
    """None — токен битый, чужой или протух: на публичном пути это аноним."""
    try:
        return jwt.decode(token, _key("jwt_public"), algorithms=["EdDSA"], options={"require": ["sub", "ver", "exp"]})
    except jwt.InvalidTokenError:
        return None


class AuthMiddleware:
    """Читает куку и дописывает user_id и token_version в контекст запроса. Никогда не отвечает 401 сама."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            token = HTTPConnection(scope).cookies.get(auth_config.cookie_name)
            claims = read_token(token) if token else None
            if claims:
                ctx = get_context()
                ctx.user_id, ctx.token_version = claims["sub"], claims["ver"]
        await self.app(scope, receive, send)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SAME_SITE_FETCH = frozenset({"same-origin", "none"})


class OriginCheckMiddleware:
    """Отклоняет изменяющие запросы с чужим Origin: CSRF-защита для куки."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None:
                foreign = origin not in auth_config.allowed_origins
            else:
                # Origin вырезан (расширение, прокси): браузер всё равно шлёт Sec-Fetch-Site; нет и его — не браузер
                foreign = headers.get("sec-fetch-site", "same-origin") not in SAME_SITE_FETCH
            if foreign:
                response = JSONResponse(
                    {"error": {"code": "bad_origin", "message": "Запрос с чужого сайта"}}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


SESSION_COLUMNS = (User.id, User.name, User.role, User.person_id, User.is_active, User.token_version)


async def load_session_user(user_id: UUID) -> SessionUser | None:
    """Пользователь по первичному ключу: блокировка и смена роли действуют сразу, без перевыпуска токена."""
    async with uow_ro_factory() as uow:
        row = (await uow.session.execute(select(*SESSION_COLUMNS).where(User.id == user_id))).one_or_none()
    return SessionUser(*row) if row else None


async def current_user(ctx: MessageContext = Depends(get_context)) -> SessionUser:
    user = await load_session_user(UUID(ctx.user_id)) if ctx.user_id else None
    if user is None or not user.is_active or user.token_version != ctx.token_version:
        raise HTTPException(401, "Нужен вход")
    return user


async def require_moderator(user: SessionUser = Depends(current_user)) -> SessionUser:
    if user.role not in ("moderator", "admin"):
        raise HTTPException(403, "Недостаточно прав")
    return user


async def require_author(user: SessionUser = Depends(current_user)) -> SessionUser:
    if user.role != "author" or user.person_id is None:
        raise HTTPException(403, "Недостаточно прав")
    return user
