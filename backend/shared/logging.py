"""JSON-логи в stdout: к каждой записи добавляются service, trace_id и user_id из контекста."""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

from shared.config import app_config
from shared.context import _context

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = _context.get()
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "service": app_config.service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": ctx.trace_id if ctx else None,
            "user_id": ctx.user_id if ctx else None,
        }
        entry |= {k: v for k, v in vars(record).items() if k not in _STANDARD}
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Один JSON-форматтер на корневой логгер; uvicorn, sqlalchemy и aio_pika пишут через него же."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "aio_pika", "aiormq"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("aiormq").setLevel(logging.WARNING)
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.captureWarnings(True)


def log_loop_errors() -> None:
    """Исключения задач без await тоже уходят в JSON-лог, а не теряются в stderr."""

    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        logging.getLogger("asyncio").error(context.get("message", "Ошибка в цикле событий"), exc_info=exc)

    asyncio.get_running_loop().set_exception_handler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
