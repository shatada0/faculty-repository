"""Люди: сопоставление авторов по ключам из person_names и поиск по началам слов."""

import re
from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import any_, func, select, update
from sqlalchemy.sql import Select

from shared.imports import ImportItem
from shared.people import Person, PersonName
from shared.people.names import exact_key, name_query, parse_name, short_key, short_name, surname_first, translit
from shared.repository.works_repository import few, norm, plain
from shared.storage import photo_url
from shared.uow import UnitOfWork
from shared.works import WorkPerson

LATIN = re.compile(r"[A-Za-z]")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def is_latin(name: str) -> bool:
    return bool(LATIN.search(name)) and not CYRILLIC.search(name)


def initials_fit(display: str, full_name: str) -> bool:
    """Инициалы написания не противоречат ФИО: «Oseledets I.» и «I. V.» подходят «Ивану Валерьевичу», «I. A.» — нет."""
    a = [translit(i).lower()[:1] for i in parse_name(display)[1]]
    b = [translit(i).lower()[:1] for i in parse_name(full_name)[1]]
    return all(x == y for x, y in zip(a, b, strict=False))


def add_name(person: Person, name: str) -> None:
    """Вариант написания с ключами сопоставления; повтор не добавляется."""
    name = name.strip()
    if name and all(n.name != name for n in person.names):
        person.names.append(PersonName(name=name, exact_key=exact_key(name), short_key=short_key(name)))


def person_dict(p: Person, *, admin: bool = False) -> dict:
    out = {
        "id": str(p.id),
        "kind": p.kind,
        "fullName": p.full_name,
        "department": p.department,
        "position": p.position,
        "academicDegree": p.academic_degree,
        "academicTitle": p.academic_title,
        "orcid": p.orcid,
        "affiliation": p.affiliation,
        "photoUrl": photo_url(p.photo_key) if p.photo_key else None,
    }
    if admin:
        out |= {"aliases": p.aliases, "shortName": short_name(p.full_name)}
    return out


def name_search(q: str) -> Select | None:
    """«Иванов И. И.» находит «Иванов Иван Иванович»: каждое слово запроса — начало слова какого-то варианта имени."""
    query = name_query(q)
    if query is None:
        return None
    name = norm(PersonName.name)
    prefix = func.to_tsvector("simple", name).bool_op("@@")(func.to_tsquery("simple", query))
    # опечатки: «Арлов» находит «Орлов», если по началам слов нашлось мало (триграммный индекс ix_person_names_trgm)
    similar = name.bool_op("%>")(plain(q)) & few(select(PersonName.person_id).where(prefix))
    return select(PersonName.person_id).where(prefix | similar).distinct()


NAMES_LOCK = 0x6E616D6573  # pg_advisory_xact_lock: варианты имён пишет один запрос за раз


async def lock_names(uow: UnitOfWork) -> None:
    """Сопоставление, слияние и заполнение по DOI добавляют варианты имён: два запроса разом упали бы на ключе."""
    await uow.session.execute(select(func.pg_advisory_xact_lock(NAMES_LOCK)))


class PersonRepository:
    async def get(self, uow: UnitOfWork, person_id: UUID) -> Person | None:
        return await uow.session.get(Person, person_id)

    async def resolve(self, uow: UnitOfWork, person_id: UUID) -> Person | None:
        """Запрос по id объединённого профиля отдаёт основной."""
        person = await uow.session.get(Person, person_id)
        for _ in range(10):
            if person is None or person.merged_into is None:
                break
            person = await uow.session.get(Person, person.merged_into)
        return person

    async def by_ids(self, uow: UnitOfWork, ids: Iterable[UUID]) -> dict[UUID, Person]:
        ids = set(ids)
        if not ids:
            return {}
        rows = (await uow.session.execute(select(Person).where(Person.id.in_(ids)))).scalars()
        return {p.id: p for p in rows}

    async def by_orcid(self, uow: UnitOfWork, orcid: str) -> Person | None:
        """Человек по ORCID; двое с одним ORCID — ошибка в данных, не угадываем."""
        rows = (
            (
                await uow.session.execute(
                    select(Person).where(Person.orcid == orcid, Person.merged_into.is_(None)).limit(2)
                )
            )
            .scalars()
            .all()
        )
        return rows[0] if len(rows) == 1 else None

    async def match_exact(self, uow: UnitOfWork, display_name: str) -> Person | None:
        """Точное совпадение с полным ФИО или известным вариантом; двое с одним ключом — не угадываем."""
        return (await self.match_many(uow, [display_name])).get(display_name)

    async def match_many(self, uow: UnitOfWork, names: Iterable[str]) -> dict[str, Person]:
        """match_exact для многих написаний одним запросом: страница нераспознанных не делает запрос на имя.

        Латинское написание без точного совпадения сверяется с русским ФИО через транслит: «Oseledets I. V.»
        → «Оселедец Иван Валерьевич». Фамилия по short_key, инициалы — совместимые, человек — единственный.
        """
        names = list(dict.fromkeys(names))
        # «Р. И. Степанов» ищется и как «Степанов Р. И.»: ключ учитывает порядок, а смысл тот же
        keys = {n: {exact_key(n), exact_key(surname_first(n))} for n in names}
        if not keys:
            return {}
        rows = await uow.session.execute(
            select(PersonName.exact_key, PersonName.person_id)
            .join(Person, Person.id == PersonName.person_id)
            .where(PersonName.exact_key.in_(set().union(*keys.values())), Person.merged_into.is_(None))
        )
        by_key: dict[str, set[UUID]] = defaultdict(set)
        for key, person_id in rows:
            by_key[key].add(person_id)
        exact = {n: set().union(*(by_key[k] for k in ks)) for n, ks in keys.items()}
        found = {n: next(iter(ids)) for n, ids in exact.items() if len(ids) == 1}
        people = await self.by_ids(uow, found.values())
        out = {n: people[i] for n, i in found.items()}
        # точных кандидатов двое и больше — не угадываем и транслитом
        return out | await self._match_latin(uow, [n for n in names if not exact[n] and is_latin(n)])

    async def _match_latin(self, uow: UnitOfWork, names: list[str]) -> dict[str, Person]:
        keys = {n: short_key(n) for n in names}
        if not keys:
            return {}
        rows = await uow.session.execute(
            select(PersonName.short_key, Person)
            .join(Person, Person.id == PersonName.person_id)
            .where(PersonName.short_key.in_(set(keys.values())), Person.merged_into.is_(None))
        )
        by_key: dict[str, dict[UUID, Person]] = defaultdict(dict)
        for key, person in rows:
            by_key[key].setdefault(person.id, person)
        out = {}
        for n, key in keys.items():
            fits = [p for p in by_key[key].values() if initials_fit(n, p.full_name)]
            if len(fits) == 1:
                out[n] = fits[0]
        return out

    async def link(self, uow: UnitOfWork, person: Person, display_name: str) -> list[UUID]:
        """Написание — вариант имени человека; нераспознанные авторы с ним во всех работах и элементах импорта —
        на этого человека. → работы, у которых изменились авторы (пересчитать search)."""
        add_name(person, person.full_name)
        add_name(person, display_name)
        await uow.session.flush()
        rows = await uow.session.execute(
            update(WorkPerson)
            .where(WorkPerson.person_id.is_(None), WorkPerson.display_name == display_name)
            .values(person_id=person.id, kind=person.kind)
            .returning(WorkPerson.work_id)
        )
        await uow.session.execute(
            update(ImportItem)
            .where(any_(ImportItem.unmatched_authors) == display_name)
            .values(unmatched_authors=func.array_remove(ImportItem.unmatched_authors, display_name))
        )
        return list({r[0] for r in rows})

    async def link_known(self, uow: UnitOfWork) -> list[tuple[Person, str, list[UUID]]]:
        """Нераспознанные написания, которые теперь однозначно совпадают с человеком (его завели, переименовали,
        слили или ORCID дал новое написание), привязываются сразу во всех работах. Под lock_names."""
        names = (
            (
                await uow.session.execute(
                    select(WorkPerson.display_name)
                    .where(WorkPerson.person_id.is_(None), WorkPerson.display_name != "")
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        matched = await self.match_many(uow, names)
        return [(p, n, await self.link(uow, p, n)) for n, p in matched.items()]

    async def suggestions(self, uow: UnitOfWork, names: Iterable[str], limit: int = 5) -> dict[str, list[Person]]:
        """Кандидаты по ключу «фамилия:инициал» для каждого написания, одним запросом."""
        keys = {n: short_key(n) for n in names}
        if not keys:
            return {}
        rows = await uow.session.execute(
            select(PersonName.short_key, Person)
            .join(Person, Person.id == PersonName.person_id)
            .where(PersonName.short_key.in_(set(keys.values())), Person.merged_into.is_(None))
            .order_by(Person.full_name, Person.id)
        )
        by_key: dict[str, dict[UUID, Person]] = defaultdict(dict)
        for key, person in rows:
            by_key[key].setdefault(person.id, person)  # у человека бывает несколько вариантов с одним ключом
        return {n: list(by_key[k].values())[:limit] for n, k in keys.items()}

    def add(self, uow: UnitOfWork, person: Person) -> None:
        uow.session.add(person)
