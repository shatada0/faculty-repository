"""Запросы к работам. Видимость в каталоге — только через published(): одно место для фильтра."""

import re
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, and_, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import Load, selectinload
from sqlalchemy.sql import Select

from shared.auth.models import User
from shared.people import PersonName
from shared.refdata import Department, Journal
from shared.uow import UnitOfWork
from shared.works import Material, Work, WorkPerson
from shared.works.schemas import title_key

# Веса как в моках: название, ключевые слова и люди, аннотация, журнал/конференция/направление.
REFRESH_SEARCH = text("""
UPDATE works w SET search =
  setweight(to_tsvector('russian', replace(lower(w.title), 'ё', 'е')), 'A') ||
  setweight(to_tsvector('russian', replace(lower(array_to_string(w.keywords, ' ') || ' ' || coalesce(p.names, '')), 'ё', 'е')), 'B') ||
  setweight(to_tsvector('russian', replace(lower(coalesce(w.annotation, '')), 'ё', 'е')), 'C') ||
  setweight(to_tsvector('russian', replace(lower(CASE w.type
      WHEN 'publication' THEN coalesce(w.details->>'venue', '')
      WHEN 'talk' THEN coalesce(w.details->'event'->>'name', '')
      ELSE concat_ws(' ', w.details->'direction'->>'name', w.details->>'discipline') END), 'ё', 'е')), 'D')
FROM (
  SELECT x.id, (SELECT string_agg(concat_ws(' ', wp.display_name, pe.full_name), ' ')
                FROM work_people wp LEFT JOIN people pe ON pe.id = wp.person_id
                WHERE wp.work_id = x.id) AS names
  FROM works x WHERE x.id = ANY(:ids)
) p
WHERE w.id = p.id
""")


def published() -> ColumnElement[bool]:
    return and_(Work.status == "published", Work.access_level != "hidden")


FEW = 5  # меньше стольких полнотекстовых совпадений — добавляем похожие по триграммам
OPERATORS = re.compile(r'"|(?:^|\s)-|\sor\s')  # синтаксис websearch_to_tsquery


def norm(s: Any) -> ColumnElement[str]:
    """Как в триграммных индексах миграции 20261019_0003: нижний регистр, ё → е."""
    return func.replace(func.lower(s), "ё", "е")


def plain(q: str) -> str:
    """Запрос для триграмм: только буквы и цифры — кавычки, минус и or триграммам не нужны."""
    return " ".join(re.findall(r"[^\W_]+", q.lower().replace("ё", "е")))


def ts_query(q: str) -> ColumnElement:
    """websearch_to_tsquery понимает кавычки, or и минус и на любом вводе не падает. ё → е, как в search.
    Лексема от 3 символов после стемминга становится префиксом: «нейр» → 'нейр':* находит «нейронные».
    Короче — точно: «ИИ» стеммится в 'и', и 'и':* нашёл бы всё, где есть слово на «и»."""
    exact = func.websearch_to_tsquery("russian", q.lower().replace("ё", "е"))
    prefixed = func.regexp_replace(cast(exact, Text), r"'(?:[^']|'')+'", r"\&:*", "g")
    # Второй проход, а не {3,} в первом: короткая лексема пропускалась бы, и шаблон схватил бы «' & '» между словами.
    # После первого прохода за кавычкой всегда «:*» и ещё знак, так что 1–2 символа — только сама лексема.
    return cast(func.regexp_replace(prefixed, r"'((?:[^']|''){1,2})':\*", r"'\1'", "g"), TSQUERY)


def fts(q: str) -> ColumnElement[bool]:
    return Work.search.bool_op("@@")(ts_query(q))


def few(exact: Select) -> ColumnElement[bool]:
    """Точных совпадений мало: подзапрос без корреляции, PostgreSQL считает его один раз на запрос."""
    return select(func.count()).select_from(exact.limit(FEW).subquery()).scalar_subquery() < FEW


def matches(q: str) -> ColumnElement[bool]:
    """Единый поиск работ для каталога, фасетов и рабочего места: полнотекстовый по префиксам,
    а если точных совпадений мало — ещё и похожие по word_similarity название и ФИО (опечатки).
    ponytail: «мало» считается по всем работам, включая черновики и скрытые, а не по выдаче с фильтрами
    и видимостью: matches общий с рабочим местом, которому черновики нужны; хватит для подсказок."""
    words = plain(q)
    if not words or OPERATORS.search(q.lower()):
        return fts(q)  # с кавычками, минусом или or человек просит точно — похожие не подмешиваем
    if max(map(len, words.split())) < 3:
        return fts(q)  # «в», «по», «ИИ»: у одной-двух букв похожи почти все названия
    similar_people = (
        select(WorkPerson.work_id)
        .join(PersonName, PersonName.person_id == WorkPerson.person_id)
        .where(norm(PersonName.name).bool_op("%>")(words))
    )
    similar = norm(Work.title).bool_op("%>")(words) | Work.id.in_(similar_people)
    return fts(q) | (similar & few(select(Work.id).where(fts(q))))


def relevance(q: str) -> tuple[ColumnElement, ...]:
    """Порядок выдачи по релевантности: точные выше похожих, внутри — ts_rank_cd, потом похожесть названия."""
    return (
        fts(q).desc(),
        func.ts_rank_cd(Work.search, ts_query(q)).desc(),
        func.word_similarity(plain(q), norm(Work.title)).desc(),
    )


def with_uploaders() -> Load:
    """Материалы с именем загрузившего. Только name: login и хэш пароля роли writer не выданы."""
    return selectinload(Work.materials).selectinload(Material.uploader).load_only(User.name)


def admin_options() -> tuple:
    """Админке нужны имена загрузивших материалы и имя файла импорта."""
    return (with_uploaders(), selectinload(Work.import_))


class WorkRepository:
    async def get(self, uow: UnitOfWork, work_id: UUID, *, admin: bool = False) -> Work | None:
        stmt = select(Work).where(Work.id == work_id).execution_options(populate_existing=True)
        if admin:
            stmt = stmt.options(*admin_options())
        return (await uow.session.execute(stmt)).scalar_one_or_none()

    async def get_published(self, uow: UnitOfWork, work_id: UUID) -> Work | None:
        return (await uow.session.execute(select(Work).where(Work.id == work_id, published()))).scalar_one_or_none()

    async def all(self, uow: UnitOfWork, stmt: Select) -> Sequence[Work]:
        return (await uow.session.execute(stmt)).scalars().all()

    def add(self, uow: UnitOfWork, work: Work) -> None:
        uow.session.add(work)

    async def ensure_department(self, uow: UnitOfWork, name: str | None) -> None:
        """Заводит кафедру в справочнике. Только для seed people (CSV сотрудников): редактор и импорт её не создают."""
        if name and await uow.session.get(Department, name) is None:
            uow.session.add(Department(name=name))
            await uow.session.flush()

    async def known_department(self, uow: UnitOfWork, name: str | None) -> str | None:
        """Кафедра, если она есть в справочнике, иначе None: опечатка или «123» из формы не станет новой кафедрой."""
        if not name:
            return None
        with uow.session.no_autoflush:  # проверяем до записи: иначе автосброс отправит «123» в базу раньше проверки
            return name if await uow.session.get(Department, name) else None

    async def refresh_search(self, uow: UnitOfWork, ids: Iterable[UUID]) -> None:
        await uow.session.flush()
        await uow.session.execute(REFRESH_SEARCH, {"ids": list(ids)})

    async def journal_lists(self, uow: UnitOfWork, works: Iterable[Work]) -> dict[UUID, list[str]]:
        """Коды списков журнала: по ISSN, а если ISSN нет или его нет в справочнике — по точному названию журнала.
        Фасет journalList (search._journal_lists) считает так же."""
        pubs = [w for w in works if w.type == "publication"]
        issns = {w.issn for w in pubs if w.issn}
        venues = {w.details.get("venue") for w in pubs if w.details.get("venue")}
        if not issns and not venues:
            return {}
        rows = (
            await uow.session.execute(select(Journal).where(or_(Journal.issn.in_(issns), Journal.name.in_(venues))))
        ).scalars()
        by_issn, by_name = {}, {}
        for j in rows:
            by_issn[j.issn] = j.lists
            by_name[j.name] = j.lists
        return {
            w.id: lists
            for w in pubs
            if (lists := by_issn[w.issn] if w.issn in by_issn else by_name.get(w.details.get("venue")))
        }

    async def find_duplicate(
        self, uow: UnitOfWork, work_type: str, title: str, year: int | None, exclude: UUID | None = None
    ) -> Work | None:
        stmt = select(Work).where(Work.type == work_type, Work.status != "rejected", Work.title_key == title_key(title))
        if year:
            stmt = stmt.where(Work.year == year)
        if exclude:
            stmt = stmt.where(Work.id != exclude)
        return (await uow.session.execute(stmt.order_by(Work.created_at).limit(1))).scalar_one_or_none()
