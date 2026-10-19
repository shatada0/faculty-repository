"""Люди: сопоставление авторов по ключам из person_names и поиск по началам слов."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from shared.people import Person, PersonName
from shared.people.names import exact_key, name_query, short_key, short_name, surname_first
from shared.repository.works_repository import few, norm, plain
from shared.storage import photo_url
from shared.uow import UnitOfWork


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

    async def match_exact(self, uow: UnitOfWork, display_name: str) -> Person | None:
        """Точное совпадение с полным ФИО или известным вариантом; двое с одним ключом — не угадываем."""
        stmt = (
            select(PersonName.person_id)
            .join(Person, Person.id == PersonName.person_id)
            # «Р. И. Степанов» ищется и как «Степанов Р. И.»: ключ учитывает порядок, а смысл тот же
            .where(
                PersonName.exact_key.in_({exact_key(display_name), exact_key(surname_first(display_name))}),
                Person.merged_into.is_(None),
            )
            .distinct()
            .limit(2)
        )
        ids = (await uow.session.execute(stmt)).scalars().all()
        return await uow.session.get(Person, ids[0]) if len(ids) == 1 else None

    async def suggestions(self, uow: UnitOfWork, display_name: str, limit: int = 5) -> list[Person]:
        stmt = (
            select(Person)
            .where(
                Person.merged_into.is_(None),
                Person.id.in_(select(PersonName.person_id).where(PersonName.short_key == short_key(display_name))),
            )
            .order_by(Person.full_name)
            .limit(limit)
        )
        return list((await uow.session.execute(stmt)).scalars())

    def add(self, uow: UnitOfWork, person: Person) -> None:
        uow.session.add(person)
