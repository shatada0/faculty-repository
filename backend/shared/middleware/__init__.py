"""Сборка HTTP-сервиса: контекст и access-лог, проверка Origin, чтение сессии, обработчики ошибок, /healthcheck."""

import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shared.auth.session import read_token
from shared.config import auth_config
from shared.context import MessageContext, get_context, use_context
from shared.exceptions import error_response, setup_exception_handlers
from shared.logging import get_logger, setup_logging

logger = get_logger("http")


def request_details(scope: Scope) -> dict:
    """Что известно о запросе: роутер дописывает шаблон пути и path_params в тот же scope."""
    headers = Headers(scope=scope)
    route = scope.get("route")
    return {
        "method": scope.get("method"),
        "path": scope.get("path"),
        "query": scope.get("query_string", b"").decode("latin-1"),
        "route": getattr(route, "path", None),
        "path_params": scope.get("path_params"),
        "ip": headers.get("x-forwarded-for") or (scope.get("client") or ("",))[0],
        "user_agent": headers.get("user-agent"),
        "content_length": headers.get("content-length"),
    }


class RequestContextMiddleware:
    """trace_id из X-Request-ID в контекст и в ответ, access-лог одной строкой, 500 на необработанное."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        trace_id = Headers(scope=scope).get("x-request-id") or uuid4().hex
        ctx = MessageContext(trace_id=trace_id)
        status: int | None = None
        started = time.perf_counter()

        async def send_traced(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", trace_id.encode())]
            await send(message)

        with use_context(ctx):
            try:
                await self.app(scope, receive, send_traced)
            except Exception:
                logger.exception("Необработанная ошибка", extra=request_details(scope))
                if status is not None:
                    raise  # ответ уже начат, заменить его нечем
                await error_response(500, "internal", "Внутренняя ошибка сервера", traceId=trace_id)(
                    scope, receive, send_traced
                )
            finally:
                if scope["path"] != "/healthcheck":
                    logger.info(
                        "HTTP-запрос",
                        extra={
                            "method": scope["method"],
                            "path": scope["path"],
                            "route": getattr(scope.get("route"), "path", None),
                            "status": status or 500,
                            "duration_ms": round((time.perf_counter() - started) * 1000),
                        },
                    )


Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    title: str, routers: list[APIRouter], *, middlewares: tuple = (), lifespan: Lifespan | None = None
) -> FastAPI:
    """FastAPI с общими обработчиками. middlewares — снаружи внутрь, после RequestContextMiddleware."""
    setup_logging()
    app = FastAPI(title=title, lifespan=lifespan, docs_url="/docs", openapi_url="/openapi.json", redoc_url=None)
    for router in routers:
        app.include_router(router)

    @app.get("/healthcheck", include_in_schema=False)
    async def healthcheck() -> dict:
        return {"status": "ok"}

    setup_exception_handlers(app)
    # Последний add_middleware выполняется первым.
    for mw in reversed(middlewares):
        app.add_middleware(mw)
    app.add_middleware(RequestContextMiddleware)
    return app


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
