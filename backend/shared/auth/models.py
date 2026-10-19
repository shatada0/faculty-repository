"""Учётные записи. Пароли читает только роль auth: у writer часть колонок, у catalog — ничего."""

from uuid import UUID, uuid7

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.db.base import Base, one_of


class User(Base):
    __tablename__ = "users"
    __table_args__ = (one_of("role", ("moderator", "admin", "author")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    login: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("people.id"))
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
