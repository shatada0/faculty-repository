"""Люди: сотрудники, студенты, выпускники и внешние авторы, все варианты написания ФИО."""

from uuid import UUID, uuid7

from sqlalchemy import (
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, one_of


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (one_of("kind", ("staff", "student", "graduate", "external")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(ForeignKey("departments.name", onupdate="CASCADE"))
    position: Mapped[str | None] = mapped_column(Text)
    academic_degree: Mapped[str | None] = mapped_column(Text)
    academic_title: Mapped[str | None] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(Text)
    spin: Mapped[str | None] = mapped_column(Text)  # SPIN-код автора в РИНЦ: 1234-5678
    elibrary_id: Mapped[str | None] = mapped_column(Text)  # AuthorID в РИНЦ: по нему страница автора и API eLIBRARY
    affiliation: Mapped[str | None] = mapped_column(Text)
    photo_key: Mapped[str | None] = mapped_column(Text)
    merged_into: Mapped[UUID | None] = mapped_column(ForeignKey("people.id"))

    names: Mapped[list[PersonName]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="PersonName.name"
    )

    @property
    def aliases(self) -> list[str]:
        return [n.name for n in self.names if n.name != self.full_name]


class PersonName(Base):
    """Полное ФИО и все варианты написания с ключами сопоставления (shared/names.py)."""

    __tablename__ = "person_names"
    __table_args__ = (
        Index("ix_person_names_exact_key", "exact_key"),
        Index("ix_person_names_short_key", "short_key"),
        Index(
            "ix_person_names_search",
            text("to_tsvector('simple', replace(lower(name), 'ё', 'е'))"),
            postgresql_using="gin",
        ),
        Index("ix_person_names_trgm", text("replace(lower(name), 'ё', 'е') gin_trgm_ops"), postgresql_using="gin"),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    exact_key: Mapped[str] = mapped_column(Text)
    short_key: Mapped[str] = mapped_column(Text)
