"""Фильтры и фасеты каталога. Внутри фасета — ИЛИ, между фасетами — И; счётчики фасета k — по выдаче без фильтра k.

Каждый фасет — подзапрос пар (work_id, value): по нему и фильтруем, и считаем. Эталон семантики — src/mocks/logic.ts.
"""

from collections.abc import Callable

from sqlalchemy import ColumnElement, Text, and_, cast, func, or_, select
from sqlalchemy.sql import Select

from shared.models import STUDENT_TYPES, Journal, Person, Work, WorkPerson, WorkSubject

D = Work.details

TAB_FILTERS: dict[str, list[str]] = {
    "all": ["year", "department", "author", "grnti"],
    "vkr": ["year", "level", "direction", "department", "supervisor", "grnti"],
    "coursework": ["year", "courseKind", "level", "direction", "discipline", "department", "supervisor", "grnti"],
    "publication": [
        "year",
        "pubKind",
        "indexing",
        "journalList",
        "venue",
        "department",
        "author",
        "grnti",
        "oecd",
        "wos",
        "asjc",
    ],
    "talk": ["year", "confLevel", "talkKind", "format", "department", "author", "grnti"],
}
ALL_FILTERS = list(dict.fromkeys(k for keys in TAB_FILTERS.values() for k in keys))


def _of(work_type: str | tuple[str, ...], value: ColumnElement) -> Select:
    types = (work_type,) if isinstance(work_type, str) else work_type
    return select(Work.id.label("work_id"), value.label("value")).where(
        Work.type.in_(types), value.is_not(None), value != ""
    )


def _people(roles: tuple[str, ...]) -> Select:
    """Только сотрудники: у них есть фасет и страница."""
    return (
        select(WorkPerson.work_id.label("work_id"), cast(WorkPerson.person_id, Text).label("value"))
        .join(Person, Person.id == WorkPerson.person_id)
        .where(WorkPerson.role.in_(roles), Person.kind == "staff")
    )


def _subjects(scheme: str) -> Select:
    return select(WorkSubject.work_id.label("work_id"), WorkSubject.code.label("value")).where(
        WorkSubject.scheme == scheme
    )


def _journal_lists() -> Select:
    venue = D["venue"].astext
    journal = or_(Journal.issn == Work.issn, and_(Work.issn.is_(None), Journal.name == venue))
    return (
        select(Work.id.label("work_id"), func.unnest(Journal.lists).label("value"))
        .join(Journal, journal)
        .where(Work.type == "publication")
    )


PAIRS: dict[str, Callable[[], Select]] = {
    "year": lambda: select(Work.id.label("work_id"), cast(Work.year, Text).label("value")).where(
        Work.year.is_not(None)
    ),
    "department": lambda: select(Work.id.label("work_id"), Work.department.label("value")).where(
        Work.department.is_not(None)
    ),
    "author": lambda: _people(("author", "presenter")),
    "supervisor": lambda: _people(("supervisor",)),
    "level": lambda: _of(STUDENT_TYPES, D["level"].astext),
    "direction": lambda: _of(STUDENT_TYPES, D[("direction", "code")].astext),
    "discipline": lambda: _of("coursework", D["discipline"].astext),
    "pubKind": lambda: _of("publication", D["kind"].astext),
    # функция-генератор в WHERE нельзя: тип массива проверяем отдельно
    "indexing": lambda: select(
        Work.id.label("work_id"), func.jsonb_array_elements_text(D["indexing"]).label("value")
    ).where(Work.type == "publication", func.jsonb_typeof(D["indexing"]) == "array"),
    "venue": lambda: _of("publication", D["venue"].astext),
    "confLevel": lambda: _of("talk", D[("event", "level")].astext),
    "talkKind": lambda: _of("talk", D["kind"].astext),
    "format": lambda: _of("talk", D["format"].astext),
    "courseKind": lambda: _of("coursework", func.coalesce(D["kind"].astext, "work")),
    "journalList": _journal_lists,
    **{scheme: (lambda s=scheme: _subjects(s)) for scheme in ("grnti", "oecd", "wos", "asjc")},
}


def selected(params: dict[str, list[str]], key: str) -> list[str]:
    return [v for v in params.get(key, []) if v][:50]


def filter_clauses(params: dict[str, list[str]], keys: list[str], skip: str | None = None) -> list[ColumnElement[bool]]:
    clauses = []
    for key in keys:
        values = selected(params, key)
        if key != skip and values:
            pairs = PAIRS[key]().subquery()
            clauses.append(Work.id.in_(select(pairs.c.work_id).where(pairs.c.value.in_(values))))
    return clauses


def facet_counts(key: str, conditions: list[ColumnElement[bool]]) -> Select:
    """Счётчики значений фасета по работам, прошедшим conditions."""
    pairs = PAIRS[key]().subquery()
    matching = select(Work.id).where(*conditions)
    return (
        select(pairs.c.value, func.count(pairs.c.work_id.distinct()))
        .where(pairs.c.work_id.in_(matching))
        .group_by(pairs.c.value)
    )
