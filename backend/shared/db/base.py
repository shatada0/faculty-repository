"""Базовая модель. id — uuid v7 из Python: растёт по времени, индекс не фрагментируется."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar = {datetime: DateTime(timezone=True)}  # время всегда timestamptz


def now() -> datetime:
    return datetime.now(UTC)


def one_of(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    """CHECK «колонка IN (...)»: имя ограничения по NAMING — ck_<таблица>_<колонка>."""
    listed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name or column)


def created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())
