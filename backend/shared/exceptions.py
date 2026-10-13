"""Ошибки домена и их перевод в ответ {error: {code, message}}; клиент ничего другого не видит."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from shared.logging import get_logger

logger = get_logger(__name__)

HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "validation_error",
}


class DomainError(Exception):
    """Ожидаемая ошибка домена: сервисный слой бросает, общий обработчик переводит в HTTP."""

    status = 400
    code = "bad_request"

    def __init__(
        self, message: str, code: str | None = None, *, fields: list[dict] | None = None, **details: object
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.fields = fields  # для 422: [{field: "accessLevel", message}]
        self.details = details


class BadRequestError(DomainError):
    status, code = 400, "bad_request"


class UnauthorizedError(DomainError):
    status, code = 401, "unauthorized"


class ForbiddenError(DomainError):
    status, code = 403, "forbidden"


class NotFoundError(DomainError):
    status, code = 404, "not_found"


class ConflictError(DomainError):
    status, code = 409, "conflict"


class UnprocessableError(DomainError):
    status, code = 422, "validation"


class UpstreamError(DomainError):
    status, code = 502, "upstream"


class RejectMessageError(Exception):
    """Сообщение шины нельзя обработать, и повтор не поможет: reject без повтора, в dead_letters."""


class RetryMessageError(Exception):
    """Временная ошибка (LLM-сервер недоступен): сообщение уходит в очередь повторов."""


def error_response(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def validation_fields(errors: list[dict], *, strip_source: bool) -> list[dict]:
    """loc pydantic → путь через точку, как ждёт фронт: event.dateEnd, authors.0.displayName."""
    out = []
    for e in errors:
        loc = list(e["loc"])
        if strip_source and loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]
        out.append({"field": ".".join(str(x) for x in loc), "message": e["msg"]})
    return out


def _validation_response(fields: list[dict]) -> JSONResponse:
    message = "; ".join(f"{f['field']}: {f['message']}" if f["field"] else f["message"] for f in fields)
    return error_response(422, "validation_error", message or "Неверный запрос", fields=fields)


def setup_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain(_: Request, exc: DomainError) -> JSONResponse:
        logger.warning(exc.message, extra={"code": exc.code, **exc.details})
        return error_response(exc.status, exc.code, exc.message, **({"fields": exc.fields} if exc.fields else {}))

    @app.exception_handler(RequestValidationError)
    async def request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Без ключа input: в теле /auth/login — пароль.
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
        logger.warning("Запрос не прошёл валидацию", extra={"errors": errors})
        return _validation_response(validation_fields(errors, strip_source=True))

    @app.exception_handler(ValidationError)
    async def model_validation(_: Request, exc: ValidationError) -> JSONResponse:
        errors = exc.errors(include_input=False, include_url=False, include_context=False)
        logger.warning("Данные не прошли валидацию", extra={"errors": errors})
        return _validation_response(validation_fields(errors, strip_source=False))

    @app.exception_handler(HTTPException)
    async def http(_: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 400:
            logger.warning(str(exc.detail), extra={"status": exc.status_code})
        message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
        if exc.status_code == 404 and message == "Not Found":
            message = "Не найдено"
        return error_response(exc.status_code, HTTP_CODES.get(exc.status_code, "http_error"), message)
