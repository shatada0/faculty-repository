"""Работы. Любой тип — одна строка works: общее колонками, своё — в details (JSONB)."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid7

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base, created, one_of

if TYPE_CHECKING:
    from shared.auth.models import User
    from shared.imports.models import Import
    from shared.refdata.models import Classifier

WORK_TYPES = ("vkr", "coursework", "publication", "talk")
STUDENT_TYPES = ("vkr", "coursework")


class Work(Base):
    __tablename__ = "works"
    __table_args__ = (
        one_of("type", WORK_TYPES),
        one_of("status", ("draft", "published", "rejected")),
        one_of("access_level", ("full", "card_only", "hidden")),
        one_of("language", ("ru", "en", "other")),
        # Открытый текст студенческой работы без согласия не сохранится даже с ошибкой в коде.
        CheckConstraint("access_level <> 'full' OR consent OR type IN ('publication', 'talk')", name="consent"),
        Index("ix_works_status_type_year", "status", "type", "year"),
        Index("ix_works_search", "search", postgresql_using="gin"),
        Index("ix_works_title_trgm", text("replace(lower(title), 'ё', 'е') gin_trgm_ops"), postgresql_using="gin"),
        Index("ix_works_type_title_key_year", "type", "title_key", "year"),
        Index("ix_works_doi", text("lower(doi)")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="draft")
    title: Mapped[str] = mapped_column(Text, default="")
    title_key: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer)
    department: Mapped[str | None] = mapped_column(ForeignKey("departments.name", onupdate="CASCADE"))
    annotation: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    language: Mapped[str] = mapped_column(Text, default="ru")
    access_level: Mapped[str] = mapped_column(Text, default="card_only")
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    external_url: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    issn: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    field_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    file_key: Mapped[str | None] = mapped_column(Text)
    original_file_name: Mapped[str | None] = mapped_column(Text)
    related_id: Mapped[UUID | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"))
    duplicate_of: Mapped[UUID | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"))
    import_id: Mapped[UUID | None] = mapped_column(ForeignKey("imports.id"))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    search: Mapped[str | None] = mapped_column(TSVECTOR, deferred=True)
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = created()
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))

    people: Mapped[list[WorkPerson]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="WorkPerson.position"
    )
    subjects: Mapped[list[WorkSubject]] = relationship(lazy="selectin", cascade="all, delete-orphan")
    citations: Mapped[list[WorkCitation]] = relationship(lazy="selectin", cascade="all, delete-orphan")
    materials: Mapped[list[Material]] = relationship(lazy="selectin", order_by="Material.uploaded_at")
    import_: Mapped[Import | None] = relationship(lazy="raise")

    def refs(self, role: str) -> list[WorkPerson]:
        return [p for p in self.people if p.role == role]


class WorkPerson(Base):
    """Авторы, руководители и докладчики в порядке из работы. person_id NULL — нераспознанный автор."""

    __tablename__ = "work_people"
    __table_args__ = (
        one_of("role", ("author", "supervisor", "presenter")),
        one_of("kind", ("staff", "student", "graduate", "external")),
        Index("ix_work_people_person_id", "person_id"),
        Index("ix_work_people_unmatched", "display_name", postgresql_where=text("person_id IS NULL")),
    )
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("people.id"))
    display_name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)


class WorkSubject(Base):
    """Рубрики ГРНТИ, ОЕСР, WoS, ASJC."""

    __tablename__ = "work_subjects"
    __table_args__ = (
        ForeignKeyConstraint(["scheme", "code"], ["classifiers.scheme", "classifiers.code"], onupdate="CASCADE"),
        Index("ix_work_subjects_scheme_code", "scheme", "code"),
    )
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    scheme: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)

    classifier: Mapped[Classifier] = relationship(lazy="joined")


class WorkCitation(Base):
    """Цитирования по источникам; citations в API — максимум из них."""

    __tablename__ = "work_citations"
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = created()


class Material(Base):
    """Файл автора к публикации или докладу; ждёт решения модератора."""

    __tablename__ = "materials"
    __table_args__ = (
        one_of("kind", ("text", "slides", "data", "other")),
        one_of("status", ("pending", "approved", "rejected")),
        Index("ix_materials_work_id", "work_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"))
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    file_key: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = created()
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]

    # Только admin_service: у роли catalog нет доступа к users.
    uploader: Mapped[User | None] = relationship(foreign_keys=[uploaded_by], lazy="raise")
