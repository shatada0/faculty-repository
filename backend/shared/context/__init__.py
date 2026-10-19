"""Контекст HTTP-запроса или сообщения шины: один trace_id проходит через всю цепочку."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class MessageContext:
    """Изменяемый: AuthMiddleware дописывает пользователя в тот же объект, что создал RequestContextMiddleware."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    user_id: str | None = None
    token_version: int | None = None


_context: ContextVar[MessageContext | None] = ContextVar("context", default=None)


def get_context() -> MessageContext:
    return _context.get() or MessageContext()


@contextmanager
def use_context(context: MessageContext) -> Iterator[None]:
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)
