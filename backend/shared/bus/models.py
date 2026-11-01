"""Outbox: событие пишется в той же транзакции, что и данные, а публикует его OutboxRelay после коммита."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.db.base import Base, created


class OutboxMessage(Base):
    __tablename__ = "outbox"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)  # uuid7: порядок публикации — порядок записи
    topic: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
