"""Импорты: загрузка, её элементы и сырой ответ ИИ по каждому полю."""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, created, one_of
from shared.works.models import WORK_TYPES


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        one_of("kind", ("archive", "table")),
        one_of("work_type", WORK_TYPES),
        one_of("status", ("uploading", "processing", "done", "failed")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(Text)
    work_type: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    meta_table_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created()
    created_by: Mapped[str] = mapped_column(Text)  # имя модератора, как Import.createdBy в контракте

    items: Mapped[list[ImportItem]] = relationship(lazy="raise", order_by="ImportItem.n")


class ImportItem(Base):
    __tablename__ = "import_items"
    __table_args__ = (
        UniqueConstraint("import_id", "n"),
        one_of("status", ("pending", "extracted", "filled", "published", "duplicate", "error")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    import_id: Mapped[UUID] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"))
    n: Mapped[int] = mapped_column(Integer)
    ref: Mapped[str] = mapped_column(Text)  # имя файла из архива или «Строка 5»
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    work_id: Mapped[UUID | None] = mapped_column(ForeignKey("works.id"))
    duplicate_of: Mapped[UUID | None] = mapped_column(ForeignKey("works.id"))
    unmatched_authors: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    file_key: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)  # строка таблицы сведений к этому файлу архива


class ExtractionResult(Base):
    """Сырой ответ воркера: что предлагала модель до правки модератора (точность ИИ на дашборде)."""

    __tablename__ = "extraction_results"
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    field: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float)
