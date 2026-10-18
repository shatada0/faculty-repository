"""SQLAlchemy-модели. Работа любого типа — одна строка works: общее колонками, своё — в details (JSONB)."""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base

WORK_TYPES = ("vkr", "coursework", "publication", "talk")
STUDENT_TYPES = ("vkr", "coursework")


def _one_of(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    listed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name or column)


def _created() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class Department(Base):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(Text, primary_key=True)


class Journal(Base):
    __tablename__ = "journals"
    issn: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    lists: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)


class Classifier(Base):
    __tablename__ = "classifiers"
    __table_args__ = (_one_of("scheme", ("grnti", "oecd", "wos", "asjc")),)
    scheme: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class Direction(Base):
    __tablename__ = "directions"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    subjects: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # «grnti:50.41»: рубрики для импорта


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (_one_of("kind", ("staff", "student", "graduate", "external")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(ForeignKey("departments.name", onupdate="CASCADE"))
    position: Mapped[str | None] = mapped_column(Text)
    academic_degree: Mapped[str | None] = mapped_column(Text)
    academic_title: Mapped[str | None] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(Text)
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
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    exact_key: Mapped[str] = mapped_column(Text)
    short_key: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (_one_of("role", ("moderator", "admin", "author")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    login: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    person_id: Mapped[UUID | None] = mapped_column(ForeignKey("people.id"))
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (
        _one_of("kind", ("archive", "table")),
        _one_of("work_type", WORK_TYPES),
        _one_of("status", ("uploading", "processing", "done", "failed")),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    kind: Mapped[str] = mapped_column(Text)
    work_type: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    meta_table_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    created_by: Mapped[str] = mapped_column(Text)  # имя модератора, как Import.createdBy в контракте

    items: Mapped[list[ImportItem]] = relationship(lazy="raise", order_by="ImportItem.n")


class ImportItem(Base):
    __tablename__ = "import_items"
    __table_args__ = (
        UniqueConstraint("import_id", "n"),
        _one_of("status", ("pending", "extracted", "filled", "published", "duplicate", "error")),
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


class Work(Base):
    __tablename__ = "works"
    __table_args__ = (
        _one_of("type", WORK_TYPES),
        _one_of("status", ("draft", "published", "rejected")),
        _one_of("access_level", ("full", "card_only", "hidden")),
        _one_of("language", ("ru", "en", "other")),
        # Открытый текст студенческой работы без согласия не сохранится даже с ошибкой в коде.
        CheckConstraint("access_level <> 'full' OR consent OR type IN ('publication', 'talk')", name="consent"),
        Index("ix_works_status_type_year", "status", "type", "year"),
        Index("ix_works_search", "search", postgresql_using="gin"),
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
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
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
        _one_of("role", ("author", "supervisor", "presenter")),
        _one_of("kind", ("staff", "student", "graduate", "external")),
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
    updated_at: Mapped[datetime] = _created()


class Material(Base):
    """Файл автора к публикации или докладу; ждёт решения модератора."""

    __tablename__ = "materials"
    __table_args__ = (
        _one_of("kind", ("text", "slides", "data", "other")),
        _one_of("status", ("pending", "approved", "rejected")),
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
    uploaded_at: Mapped[datetime] = _created()
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]

    # Только admin_service: у роли catalog нет доступа к users.
    uploader: Mapped[User | None] = relationship(foreign_keys=[uploaded_by], lazy="raise")


class ExtractionResult(Base):
    """Сырой ответ воркера: что предлагала модель до правки модератора (точность ИИ на дашборде)."""

    __tablename__ = "extraction_results"
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), primary_key=True)
    field: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float)
