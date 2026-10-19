"""Публичный каталог: только чтение и только опубликованное (published())."""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.sql import ColumnElement

from catalog_service.src.service.gost import gost_citation
from catalog_service.src.service.search import ALL_FILTERS, TAB_FILTERS, facet_counts, filter_clauses, selected
from catalog_service.src.service.view import work_view
from shared.exceptions import NotFoundError
from shared.people import Person
from shared.people.names import short_name
from shared.refdata import Classifier, Direction
from shared.repository.people_repository import PersonRepository, name_search, person_dict
from shared.repository.works_repository import WorkRepository, matches, norm, plain, published, relevance, ts_query
from shared.storage import Storage
from shared.uow import UnitOfWorkFactory
from shared.works import STUDENT_TYPES, WORK_TYPES, Work, WorkPerson
from shared.works import labels as L
from shared.works.schemas import drop_none, material_dict, work_dict

Params = dict[str, list[str]]
AUTHOR_ROLES = ("author", "presenter")
# всё название целиком, совпавшие слова между \x01 и \x02 — таких символов в названиях нет
HEADLINE = "HighlightAll=true, StartSel=\x01, StopSel=\x02"


def _ru(s: str) -> str:
    return s.lower().replace("ё", "е")


def _role_clause(person_id: UUID, role: str) -> ColumnElement[bool]:
    """Автор — среди авторов (у докладов и докладчиков); руководитель — у студенческих работ."""
    roles = ("supervisor",) if role == "supervisor" else AUTHOR_ROLES
    clause = Work.id.in_(
        select(WorkPerson.work_id).where(WorkPerson.person_id == person_id, WorkPerson.role.in_(roles))
    )
    return clause & Work.type.in_(STUDENT_TYPES) if role == "supervisor" else clause


def _involved(w: Work, person_id: UUID, role: str) -> bool:
    if role == "supervisor":
        return w.type in STUDENT_TYPES and any(p.person_id == person_id for p in w.refs("supervisor"))
    return any(p.person_id == person_id for p in w.people if p.role in AUTHOR_ROLES)


@dataclass(slots=True)
class CatalogService:
    uow_factory: UnitOfWorkFactory
    works: WorkRepository
    people: PersonRepository
    storage: Storage

    # ---------- работы ----------

    async def _public(self, uow: Any, works: list[Work]) -> list[dict]:
        lists = await self.works.journal_lists(uow, works)
        out = []
        for w in works:
            data = work_dict(w, [L.JOURNAL_LISTS.get(c, c) for c in lists[w.id]] if w.id in lists else None)
            data.pop("consent", None)
            approved = [material_dict(m, "") for m in w.materials if m.status == "approved"]
            data["view"] = work_view(data, approved)
            out.append(drop_none(data))
        return out

    async def stats(self) -> dict:
        async with self.uow_factory() as uow:
            rows = (
                await uow.session.execute(select(Work.type, func.count()).where(published()).group_by(Work.type))
            ).all()
            staff = await uow.session.scalar(
                select(func.count()).select_from(Person).where(Person.kind == "staff", Person.merged_into.is_(None))
            )
        return {"byType": {t: 0 for t in WORK_TYPES} | dict(rows), "staff": staff}

    async def catalog(
        self,
        params: Params,
        *,
        q: str | None,
        type_: str | None,
        exclude: UUID | None,
        sort: str | None,
        page: int,
        page_size: int,
    ) -> dict:
        conditions = [published(), *filter_clauses(params, ALL_FILTERS)]
        if q:
            conditions.append(matches(q))
        if type_:
            conditions.append(Work.type == type_)
        if exclude:
            conditions.append(Work.id != exclude)
        sort = sort or ("relevance" if q else "new")
        newest = (Work.year.desc().nulls_last(), Work.published_at.desc().nulls_last(), Work.id)
        if sort == "relevance" and q:
            order = (*relevance(q), *newest)
        elif sort == "title":
            order = (Work.title.collate("ru-RU-x-icu"), Work.id)
        else:
            order = newest
        stmt = (
            select(Work, func.count().over().label("total"))
            .where(*conditions)
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.uow_factory() as uow:
            rows = (await uow.session.execute(stmt)).all()
            total = (
                rows[0].total
                if rows
                else await uow.session.scalar(select(func.count()).select_from(Work).where(*conditions))
            )
            items = await self._public(uow, [r.Work for r in rows])
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def latest(self, n: int = 6) -> list[dict]:
        """Новые поступления: сначала самая свежая работа каждого типа, потом просто свежие — список разных типов."""
        order = (Work.published_at.desc().nulls_last(), Work.id.desc())
        async with self.uow_factory() as uow:
            first = []
            for work_type in WORK_TYPES:
                newest = select(Work).where(published(), Work.type == work_type).order_by(*order).limit(1)
                first += (await uow.session.execute(newest)).scalars().all()
            recent = (await uow.session.execute(select(Work).where(published()).order_by(*order).limit(n))).scalars()
            chosen = {w.id: w for w in first}
            for w in recent:
                if len(chosen) < n:
                    chosen.setdefault(w.id, w)
            items = sorted(
                chosen.values(), key=lambda w: w.published_at.isoformat() if w.published_at else "", reverse=True
            )
            return await self._public(uow, items[:n])

    async def facets(self, params: Params, *, q: str | None, type_: str | None) -> dict[str, list[dict]]:
        tab = type_ or "all"
        base = [published(), *([matches(q)] if q else [])]
        async with self.uow_factory() as uow:
            common = [*base, *filter_clauses(params, TAB_FILTERS["all"])]
            by_type = dict(
                (await uow.session.execute(select(Work.type, func.count()).where(*common).group_by(Work.type))).all()
            )
            out = {"type": [{"value": t, "label": t, "count": by_type.get(t, 0)} for t in WORK_TYPES]}
            of_type = [*base, *([Work.type == tab] if tab != "all" else [])]
            keys = TAB_FILTERS[tab]
            counts = {}
            for key in keys:
                rows = (
                    await uow.session.execute(facet_counts(key, [*of_type, *filter_clauses(params, keys, skip=key)]))
                ).all()
                counts[key] = dict(rows)
                for v in selected(params, key):
                    counts[key].setdefault(
                        v, 0
                    )  # выбранное без совпадений остаётся с нулём, чтобы его можно было снять
            labels = await self._facet_labels(uow, counts)
        for key, values in counts.items():
            items = [{"value": v, "label": labels.get((key, v), v), "count": c} for v, c in values.items()]
            if key == "year":
                items.sort(key=lambda x: -int(x["value"]) if x["value"].isdigit() else 0)
            else:
                items.sort(key=lambda x: (-x["count"], _ru(x["label"])))
            out[key] = items
        return out

    async def suggest(self, q: str) -> dict:
        """Подсказки строки поиска: до 6 работ с подсветкой совпадения в названии и до 4 сотрудников.
        Поиск тот же, что у выдачи (matches/relevance), так что подсказки не расходятся с полной выдачей."""
        q = q.strip()
        if not q:
            return {"works": [], "people": []}
        marked = func.ts_headline("russian", Work.title, ts_query(q), HEADLINE)
        works = (
            select(Work.id, Work.type, Work.title, Work.year, marked.label("marked"))
            .where(published(), matches(q))
            .order_by(*relevance(q), Work.year.desc().nulls_last(), Work.id)
            .limit(6)
        )
        people = select(Person.id, Person.full_name).where(Person.kind == "staff", Person.merged_into.is_(None))
        async with self.uow_factory() as uow:
            rows = (await uow.session.execute(works)).all()
            found = []
            if (ids := name_search(q)) is not None:
                order = (func.word_similarity(plain(q), norm(Person.full_name)).desc(), Person.full_name)
                stmt = people.where(Person.id.in_(ids)).order_by(*order).limit(4)
                found = (await uow.session.execute(stmt)).all()
        return {
            "works": [
                drop_none(
                    {"id": str(r.id), "type": r.type, "title": r.title, "year": r.year, "parts": _parts(r.marked)}
                )
                for r in rows
            ],
            "people": [{"id": str(p.id), "fullName": p.full_name} for p in found],
        }

    async def _facet_labels(self, uow: Any, counts: dict[str, dict[str, int]]) -> dict[tuple[str, str], str]:
        fixed = {
            "courseKind": L.COURSEWORK_KIND,
            "journalList": L.JOURNAL_LISTS,
            "level": L.LEVEL,
            "pubKind": L.PUB_KIND,
            "indexing": L.INDEXING,
            "confLevel": L.CONF_LEVEL,
            "talkKind": L.TALK_KIND,
            "format": L.TALK_FORMAT,
        }
        out = {(k, v): fixed[k].get(v, v) for k, values in counts.items() if k in fixed for v in values}
        person_ids = {UUID(v) for k in ("author", "supervisor") for v in counts.get(k, {}) if _is_uuid(v)}
        names = {str(p.id): short_name(p.full_name) for p in (await self.people.by_ids(uow, person_ids)).values()}
        out |= {(k, v): names.get(v, v) for k in ("author", "supervisor") for v in counts.get(k, {})}
        schemes = [k for k in ("grnti", "oecd", "wos", "asjc") if counts.get(k)]
        if schemes:
            rows = (await uow.session.execute(select(Classifier).where(Classifier.scheme.in_(schemes)))).scalars()
            names = {(c.scheme, c.code): c.name for c in rows}
            out |= {(k, v): f"{v} {names.get((k, v), '')}".strip() for k in schemes for v in counts[k]}
        if counts.get("direction"):
            rows = (
                await uow.session.execute(select(Direction).where(Direction.code.in_(counts["direction"])))
            ).scalars()
            out |= {("direction", d.code): f"{d.code} {d.name}" for d in rows}
        return out

    async def work(self, work_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            w = await self.works.get_published(uow, work_id)
            if w is None:
                raise NotFoundError("Работа не найдена")
            return (await self._public(uow, [w]))[0]

    async def file(self, work_id: UUID) -> dict:
        """Ссылка только на опубликованную работу с открытым текстом, иначе 404."""
        async with self.uow_factory() as uow:
            w = await self.works.get_published(uow, work_id)
        if w is None or w.access_level != "full" or not w.file_key:
            raise NotFoundError("Текст недоступен")
        return await self.storage.presign(
            w.file_key, w.original_file_name or f"{w.id}.pdf", inline=w.file_key.endswith(".pdf")
        )

    async def citation(self, work_id: UUID) -> dict:
        data = await self.work(work_id)
        return {"gost": gost_citation(data)}

    async def material(self, work_id: UUID, material_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            w = await self.works.get_published(uow, work_id)
        m = next((m for m in w.materials if m.id == material_id and m.status == "approved"), None) if w else None
        if m is None:
            raise NotFoundError("Материал не найден")
        return await self.storage.presign(m.file_key, m.name, inline=False)

    # ---------- люди ----------

    async def _work_counts(self, uow: Any, person_ids: list[UUID] | None = None) -> dict[UUID, dict[str, int]]:
        """Публикации и выступления — как автор, ВКР и курсовые — как руководитель; только видимые работы."""
        role_fits = (Work.type.in_(("publication", "talk")) & WorkPerson.role.in_(AUTHOR_ROLES)) | (
            Work.type.in_(STUDENT_TYPES) & (WorkPerson.role == "supervisor")
        )
        stmt = (
            select(WorkPerson.person_id, Work.type, func.count(Work.id.distinct()))
            .join(Work, Work.id == WorkPerson.work_id)
            .where(published(), role_fits, WorkPerson.person_id.is_not(None))
            .group_by(WorkPerson.person_id, Work.type)
        )
        if person_ids is not None:
            stmt = stmt.where(WorkPerson.person_id.in_(person_ids))
        out: dict[UUID, dict[str, int]] = defaultdict(
            lambda: dict.fromkeys(("publication", "talk", "vkr", "coursework"), 0)
        )
        for pid, t, c in (await uow.session.execute(stmt)).all():
            out[pid][t] = c
        return out

    async def departments(self) -> dict:
        async with self.uow_factory() as uow:
            staff = (
                (
                    await uow.session.execute(
                        select(Person).where(
                            Person.kind == "staff", Person.merged_into.is_(None), Person.department.is_not(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            # то же, что фасет «Кафедра» в каталоге: опубликованные работы всех типов с этой кафедрой
            stmt = (
                select(Work.department, func.count())
                .where(published(), Work.department.is_not(None))
                .group_by(Work.department)
            )
            work_counts = dict((await uow.session.execute(stmt)).all())
        by_dept: dict[str, list[Person]] = defaultdict(list)
        for p in sorted(staff, key=lambda p: _ru(p.full_name)):
            by_dept[p.department].append(p)
        return {
            "items": [
                {
                    "name": name,
                    "staffCount": len(people),
                    "workCount": work_counts.get(name, 0),
                    "people": [{"id": str(p.id), "shortName": short_name(p.full_name)} for p in people[:3]],
                }
                for name, people in sorted(by_dept.items(), key=lambda x: _ru(x[0]))
            ]
        }

    async def staff(self, *, q: str | None, departments: list[str], sort: str | None) -> dict:
        stmt = select(Person).where(Person.kind == "staff", Person.merged_into.is_(None))
        if q and (ids := name_search(q)) is not None:
            stmt = stmt.where(Person.id.in_(ids))
        if departments:
            stmt = stmt.where(Person.department.in_(departments))
        async with self.uow_factory() as uow:
            people = (await uow.session.execute(stmt)).scalars().all()
            counts = await self._work_counts(uow, [p.id for p in people])
        items = [person_dict(p) | {"counts": counts[p.id]} for p in people]
        if sort == "works":
            items.sort(key=lambda x: (-(x["counts"]["publication"] + x["counts"]["talk"]), _ru(x["fullName"])))
        else:
            items.sort(key=lambda x: _ru(x["fullName"]))
        return {"items": [drop_none(x) for x in items]}

    async def _person(self, uow: Any, person_id: UUID) -> Person:
        """Страницы есть у преподавателей, студентов и выпускников; у внешних авторов — нет."""
        p = await self.people.resolve(uow, person_id)
        if p is None or p.kind == "external":
            raise NotFoundError("Человек не найден")
        return p

    async def profile(self, person_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            p = await self._person(uow, person_id)
            involved = Work.id.in_(select(WorkPerson.work_id).where(WorkPerson.person_id == p.id))
            works = list((await uow.session.execute(select(Work).where(published(), involved))).scalars())
            coauthor_ids = {
                a.person_id
                for w in works
                if w.type in ("publication", "talk") and _involved(w, p.id, "author")
                for a in w.refs("author")
                if a.person_id and a.person_id != p.id
            }
            coauthors = await self.people.by_ids(uow, coauthor_ids)
        return drop_none(person_dict(p) | _profile_parts(p, works, coauthors))

    async def person_works(self, person_id: UUID, params: Params, *, type_: str | None, role: str) -> dict:
        async with self.uow_factory() as uow:
            p = await self._person(uow, person_id)
            conditions = [
                published(),
                _role_clause(p.id, role),
                *filter_clauses(params, ["year", "pubKind", "indexing", "talkKind", "confLevel"]),
            ]
            if type_:
                conditions.append(Work.type == type_)
            stmt = (
                select(Work)
                .where(*conditions)
                .order_by(Work.year.desc().nulls_last(), Work.title.collate("ru-RU-x-icu"))
            )
            works = list((await uow.session.execute(stmt)).scalars())
            return {"items": await self._public(uow, works), "total": len(works)}

    async def metrics(self, person_id: UUID, year: str | None) -> dict:
        """Индекс Хирша и цитирования по опубликованным публикациям человека — по репозиторию, не по Scopus."""
        async with self.uow_factory() as uow:
            p = await self._person(uow, person_id)
            stmt = select(Work).where(published(), Work.type == "publication", _role_clause(p.id, "author"))
            pubs = list((await uow.session.execute(stmt)).scalars())
        if not pubs:
            return {"items": [], "years": []}
        chosen = [w for w in pubs if str(w.year) == year] if year else pubs
        cites = sorted((max((c.count for c in w.citations), default=0) for w in chosen), reverse=True)
        h = sum(1 for i, n in enumerate(cites) if n >= i + 1)
        total = sum(cites)
        return {
            "items": [
                {"value": h, "label": "индекс Хирша", "note": f"по работам {year} года" if year else "по всем годам"},
                {
                    "value": total,
                    "label": L.plural(total, ("цитирование", "цитирования", "цитирований")),
                    "note": f"{len(chosen)} {L.plural(len(chosen), L.FORMS['publication'])}",
                },
            ],
            "years": sorted({w.year for w in pubs if w.year}, reverse=True),
        }


def _parts(marked: str) -> list[dict]:
    """Название из ts_headline с метками \\x01…\\x02 → куски с флагом совпадения: фронт только красит."""
    return [{"text": t, "hit": i % 2 == 1} for i, t in enumerate(re.split("[\x01\x02]", marked)) if t]


def _is_uuid(v: str) -> bool:
    try:
        UUID(v)
    except ValueError:
        return False
    return True


def _options(labels: dict[str, str]) -> list[dict]:
    return [{"value": k, "label": v} for k, v in labels.items()]


def _breakdown(counter: Counter, labels: dict[str, str]) -> str:
    return ", ".join(f"{label} {counter[k]}" for k, label in labels.items() if counter[k] > 0)


def _profile_parts(p: Person, works: list[Work], coauthors: dict[UUID, Person]) -> dict[str, Any]:
    """Плитки, вкладки с их фильтрами, публикации и выступления по годам, соавторы. Перенос profile() из моков."""
    staff = p.kind == "staff"

    def tab(key: str, label: str, work_type: str, role: str, filters: list[dict] | None = None) -> dict:
        items = [w for w in works if w.type == work_type and _involved(w, p.id, role)]
        years = sorted({w.year for w in items if w.year}, reverse=True)
        year_filter = {
            "key": "year",
            "label": "Год",
            "all": "все",
            "options": [{"value": str(y), "label": str(y)} for y in years],
        }
        return {
            "key": key,
            "label": label,
            "count": len(items),
            "query": {"type": work_type, "role": role},
            "filters": [year_filter, *(filters or [])],
        }

    pub_filters = [
        {"key": "pubKind", "label": "Вид", "all": "все", "options": _options(L.PUB_KIND)},
        {"key": "indexing", "label": "Индексация", "all": "любая", "options": _options(L.INDEXING)},
    ]
    talk_filters = [{"key": "talkKind", "label": "Вид доклада", "all": "все", "options": _options(L.TALK_KIND)}]
    if staff:
        tabs = [
            tab("publications", "Публикации", "publication", "author", pub_filters),
            tab("talks", "Выступления", "talk", "author", talk_filters),
            tab("vkr", "Руководство ВКР", "vkr", "supervisor"),
            tab("coursework", "Руководство курсовыми", "coursework", "supervisor"),
        ]
    else:
        tabs = [
            t
            for t in (
                tab("vkr", "ВКР", "vkr", "author"),
                tab("coursework", "Курсовые", "coursework", "author"),
                tab("publications", "Публикации", "publication", "author", pub_filters),
                tab("talks", "Выступления", "talk", "author", talk_filters),
            )
            if t["count"] > 0
        ]

    mine = [w for w in works if w.type in ("publication", "talk") and _involved(w, p.id, "author")]
    by_indexing: Counter = Counter()
    by_level: Counter = Counter()
    years: dict[int, dict[str, int]] = {}
    co: Counter = Counter()
    for w in mine:
        if w.type == "publication":
            by_indexing.update(w.details.get("indexing") or [])
        else:
            by_level[(w.details.get("event") or {}).get("level")] += 1
        if w.year:
            years.setdefault(w.year, {"publication": 0, "talk": 0})[w.type] += 1
        for pid in {a.person_id for a in w.refs("author")}:
            if pid and pid != p.id and pid in coauthors and coauthors[pid].kind != "external":
                co[pid] += 1
    by_year = (
        [{"year": y, **years.get(y, {"publication": 0, "talk": 0})} for y in range(min(years), max(years) + 1)]
        if years
        else []
    )
    c = {
        t: sum(1 for w in works if w.type == t and _involved(w, p.id, "supervisor" if t in STUDENT_TYPES else "author"))
        for t in WORK_TYPES
    }
    if staff:
        tiles = [
            {
                "value": c["publication"],
                "label": L.plural(c["publication"], L.FORMS["publication"]),
                "note": _breakdown(by_indexing, L.INDEXING),
            },
            {
                "value": c["talk"],
                "label": L.plural(c["talk"], L.FORMS["talk"]),
                "note": _breakdown(by_level, L.CONF_LEVEL),
            },
            {"value": c["vkr"], "label": "ВКР под руководством"},
            {"value": c["coursework"], "label": f"{L.plural(c['coursework'], L.FORMS['coursework'])} под руководством"},
        ]
        headline = p.position
    else:
        tiles = [{"value": t["count"], "label": L.plural(t["count"], L.FORMS[t["query"]["type"]])} for t in tabs]
        headline = " · ".join(x for x in (L.PERSON_KIND[p.kind], p.department) if x)
    top = sorted(co.items(), key=lambda x: (-x[1], _ru(coauthors[x[0]].full_name)))[:8]
    return {
        "headline": headline,
        "tiles": tiles,
        "tabs": tabs,
        "byYear": by_year,
        "coauthors": [{"id": str(pid), "fullName": coauthors[pid].full_name, "count": n} for pid, n in top],
    }
