"""Справочники: кафедры, журналы, классификаторы, направления подготовки."""

from sqlalchemy import (
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from shared.db.base import Base, one_of


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
    __table_args__ = (one_of("scheme", ("grnti", "oecd", "wos", "asjc")),)
    scheme: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class Direction(Base):
    __tablename__ = "directions"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    subjects: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # «grnti:50.41»: рубрики для импорта
