"""Запросы к работам. Видимость в каталоге — только через published(): одно место для фильтра."""

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, text
from sqlalchemy.orm import Load, selectinload
from sqlalchemy.sql import Select

from shared.db.uow import UnitOfWork
from shared.models import Department, Journal, Material, User, Work
from shared.works import title_key

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


def ts_query(q: str) -> ColumnElement:
    """websearch_to_tsquery понимает кавычки, or и минус и на любом вводе не падает. ё → е, как в search."""
    return func.websearch_to_tsquery("russian", q.lower().replace("ё", "е"))


def matches(q: str) -> ColumnElement[bool]:
    return Work.search.op("@@")(ts_query(q))


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
        """Кафедры — ссылки по имени; новая кафедра из редактора или импорта появляется в справочнике сама."""
        if name and await uow.session.get(Department, name) is None:
            uow.session.add(Department(name=name))
            await uow.session.flush()

    async def refresh_search(self, uow: UnitOfWork, ids: Iterable[UUID]) -> None:
        await uow.session.flush()
        await uow.session.execute(REFRESH_SEARCH, {"ids": list(ids)})

    async def journal_lists(self, uow: UnitOfWork, works: Iterable[Work]) -> dict[UUID, list[str]]:
        """Коды списков журнала: по ISSN, а без ISSN — по точному названию журнала."""
        pubs = [w for w in works if w.type == "publication"]
        issns = {w.issn for w in pubs if w.issn}
        venues = {w.details.get("venue") for w in pubs if not w.issn and w.details.get("venue")}
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
            if (lists := by_issn.get(w.issn) if w.issn else by_name.get(w.details.get("venue")))
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
