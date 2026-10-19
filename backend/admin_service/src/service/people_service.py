"""Люди в рабочем месте: поиск, нераспознанные авторы, сопоставление, правка, слияние профилей."""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import any_, func, select, update

from admin_service.src.dto import PersonIn
from shared.auth.models import User
from shared.auth.session import SessionUser
from shared.exceptions import BadRequestError, NotFoundError
from shared.imports import ImportItem
from shared.logging import get_logger
from shared.people import Person
from shared.repository.people_repository import PersonRepository, add_name, name_search, person_dict
from shared.repository.works_repository import WorkRepository
from shared.uow import UnitOfWork, UnitOfWorkFactory
from shared.works import Work, WorkPerson

logger = get_logger(__name__)

NAMES_LOCK = 0x6E616D6573  # pg_advisory_xact_lock: варианты имён пишет один запрос за раз
ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
FILLABLE = ("department", "position", "academic_degree", "academic_title", "orcid", "affiliation", "photo_key")
COLUMNS = {
    "kind": "kind",
    "fullName": "full_name",
    "department": "department",
    "position": "position",
    "academicDegree": "academic_degree",
    "academicTitle": "academic_title",
    "orcid": "orcid",
    "affiliation": "affiliation",
}


async def _lock_names(uow: UnitOfWork) -> None:
    """Сопоставление и слияние добавляют варианты имён: два запроса разом упали бы на ключе person_names."""
    await uow.session.execute(select(func.pg_advisory_xact_lock(NAMES_LOCK)))


async def relink_unmatched(uow: UnitOfWork, display_name: str, person_id: UUID, kind: str) -> list[UUID]:
    """Нераспознанный автор с этим написанием → человек во всех работах. Возвращает затронутые работы."""
    rows = await uow.session.execute(
        update(WorkPerson)
        .where(WorkPerson.person_id.is_(None), WorkPerson.display_name == display_name)
        .values(person_id=person_id, kind=kind)
        .returning(WorkPerson.work_id)
    )
    return list({r[0] for r in rows})


def _validate(body: PersonIn, *, new: bool) -> None:
    if (new or "fullName" in body.model_fields_set) and not (body.fullName or "").strip():
        raise BadRequestError("Укажите ФИО", code="bad_name")
    if body.orcid and not ORCID.fullmatch(body.orcid):
        raise BadRequestError("ORCID в формате 0000-0000-0000-0000", code="bad_orcid")


@dataclass(slots=True)
class PeopleService:
    uow_factory: UnitOfWorkFactory
    people: PersonRepository
    works: WorkRepository

    async def _resolve(self, uow: UnitOfWork, person_id: UUID) -> Person:
        p = await self.people.resolve(uow, person_id)
        if p is None:
            raise NotFoundError("Человек не найден")
        return p

    async def _work_ids(self, uow: UnitOfWork, person_id: UUID) -> list[UUID]:
        return list(
            (
                await uow.session.execute(
                    select(WorkPerson.work_id).where(WorkPerson.person_id == person_id).distinct()
                )
            ).scalars()
        )

    async def list(
        self,
        *,
        q: str | None,
        kind: str | None,
        department: str | None,
        exclude: UUID | None,
        page: int,
        page_size: int,
    ) -> dict:
        conditions = [Person.merged_into.is_(None)]
        if q and (ids := name_search(q)) is not None:
            conditions.append(Person.id.in_(ids))
        if kind:
            conditions.append(Person.kind == kind)
        if department:
            conditions.append(Person.department == department)
        if exclude:
            conditions.append(Person.id != exclude)
        stmt = (
            select(Person)
            .where(*conditions)
            .order_by(Person.full_name.collate("ru-RU-x-icu"), Person.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        async with self.uow_factory() as uow:
            people = (await uow.session.execute(stmt)).scalars().all()
            # отдельным запросом: count() over () на странице за последней дал бы total 0
            total = await uow.session.scalar(select(func.count()).select_from(Person).where(*conditions))
            ids = [p.id for p in people]
            counts = dict(
                (
                    await uow.session.execute(
                        select(WorkPerson.person_id, func.count(WorkPerson.work_id.distinct()))
                        .where(WorkPerson.person_id.in_(ids))
                        .group_by(WorkPerson.person_id)
                    )
                ).all()
            )
        items = [person_dict(p, admin=True) | {"workCount": counts.get(p.id, 0)} for p in people]
        return {"items": items, "total": total}

    async def unmatched(self, user: SessionUser) -> dict:
        """Нераспознанные авторы во всех работах с подсказками по ключу «фамилия:инициал».

        Написание, которое теперь точно совпадает с человеком (его завели позже или добавили вариант имени),
        привязывается здесь же: импорт сверяет имена только в момент загрузки.
        """
        stmt = (
            select(
                WorkPerson.display_name,
                func.array_agg(func.jsonb_build_object("id", Work.id, "title", Work.title).distinct()),
            )
            .join(Work, Work.id == WorkPerson.work_id)
            .where(WorkPerson.person_id.is_(None), WorkPerson.display_name != "")
            .group_by(WorkPerson.display_name)
            .order_by(WorkPerson.display_name.collate("ru-RU-x-icu"))
        )
        async with self.uow_factory() as uow:
            await _lock_names(uow)  # до чтения: вторая вкладка увидит уже привязанные имена, а не упадёт на ключе
            rows = (await uow.session.execute(stmt)).all()
            names = [name for name, _ in rows]
            matched = await self.people.match_many(uow, names)
            for name in names:
                if name in matched:
                    await self._link(uow, matched[name], name, user)
            rows = [(name, works) for name, works in rows if name not in matched]
            suggestions = await self.people.suggestions(uow, [name for name, _ in rows])
            items = []
            for name, works in rows:
                items.append(
                    {
                        "displayName": name,
                        "count": len(works),
                        "workIds": [w["id"] for w in works],
                        "works": [{"id": w["id"], "title": w["title"] or "Без названия"} for w in works],
                        "suggestions": [person_dict(p, admin=True) for p in suggestions[name]],
                    }
                )
        return {"items": items, "total": len(items)}

    async def _link(self, uow: UnitOfWork, p: Person, display_name: str, user: SessionUser) -> list[UUID]:
        """Написание — вариант имени человека, ссылки во всех работах и элементах импорта — на него."""
        add_name(p, p.full_name)
        add_name(p, display_name)
        await uow.session.flush()
        work_ids = await relink_unmatched(uow, display_name, p.id, p.kind)
        await uow.session.execute(
            update(ImportItem)
            .where(any_(ImportItem.unmatched_authors) == display_name)
            .values(unmatched_authors=func.array_remove(ImportItem.unmatched_authors, display_name))
        )
        await self.works.refresh_search(uow, work_ids)
        logger.info(
            "Автор сопоставлен",
            extra={
                "audit": True,
                "person_id": str(p.id),
                "display_name": display_name,
                "works": len(work_ids),
                "by": str(user.id),
            },
        )
        return work_ids

    async def create(self, body: PersonIn) -> dict:
        _validate(body, new=True)
        person = Person(kind=body.kind or "external")
        async with self.uow_factory() as uow:
            await self._fill(uow, person, body)
            self.people.add(uow, person)
        logger.info("Человек создан", extra={"person_id": str(person.id)})
        return person_dict(person, admin=True)

    async def _fill(self, uow: UnitOfWork, person: Person, body: PersonIn) -> None:
        previous = person.full_name
        for key in body.model_fields_set & COLUMNS.keys():
            value = getattr(body, key)
            value = value.strip() or None if isinstance(value, str) else value
            if value is None and key in ("kind", "fullName"):
                continue
            setattr(person, COLUMNS[key], value)
        person.full_name = person.full_name.strip()
        if body.aliases is not None:
            # прежнее ФИО остаётся вариантом: старые работы и будущий импорт под старой фамилией найдут человека
            keep = {person.full_name, previous, *(a.strip() for a in body.aliases if a.strip())} - {None}
            person.names = [n for n in person.names if n.name in keep]
            for alias in body.aliases:
                add_name(person, alias)
        add_name(person, person.full_name)
        if person.department and not await self.works.known_department(uow, person.department):
            raise BadRequestError(
                f"Кафедры «{person.department}» нет в справочнике: выберите из списка", code="bad_department"
            )

    async def card(self, person_id: UUID) -> dict:
        async with self.uow_factory() as uow:
            p = await self._resolve(uow, person_id)
            works = (
                await uow.session.execute(
                    select(Work.id, Work.type, Work.title, Work.year, Work.status)
                    .where(Work.id.in_(select(WorkPerson.work_id).where(WorkPerson.person_id == p.id)))
                    .order_by(Work.year.desc().nulls_last(), Work.title)
                )
            ).all()
        return person_dict(p, admin=True) | {
            "works": [
                {"id": str(i), "type": t, "title": title, "year": year, "status": status}
                for i, t, title, year, status in works
            ]
        }

    async def patch(self, person_id: UUID, body: PersonIn) -> dict:
        _validate(body, new=False)
        async with self.uow_factory() as uow:
            await _lock_names(uow)
            p = await self._resolve(uow, person_id)
            await self._fill(uow, p, body)
            await uow.session.execute(update(WorkPerson).where(WorkPerson.person_id == p.id).values(kind=p.kind))
            await self.works.refresh_search(uow, await self._work_ids(uow, p.id))  # имя ищется в работах человека
        return await self.card(p.id)

    async def match(self, display_name: str, person_id: UUID | None, kind: str, user: SessionUser) -> dict:
        """«Это он» / «Создать нового» / «Внешний автор»: написание становится вариантом имени, ссылки в работах — на человека."""
        display_name = display_name.strip()
        if not display_name:
            raise BadRequestError("Укажите ФИО", code="bad_name")
        async with self.uow_factory() as uow:
            await _lock_names(uow)
            if person_id:
                p = await self._resolve(uow, person_id)
            else:
                p = Person(kind=kind, full_name=display_name)
                self.people.add(uow, p)
            await self._link(uow, p, display_name, user)
        return person_dict(p, admin=True)

    async def merge(self, main_id: UUID, from_id: UUID, user: SessionUser) -> dict:
        """Одна транзакция: ссылки и учётки — на основного, имена — к нему, пустые поля — из второго. Отмены нет."""
        async with self.uow_factory() as uow:
            # Блокировка до чтения: встречные слияния A→B и B→A по очереди, второе увидит первое и не замкнёт цикл
            await _lock_names(uow)
            main = await self._resolve(uow, main_id)
            other = await self._resolve(uow, from_id)
            if main.id == other.id:
                raise BadRequestError("Нельзя объединить профиль с самим собой", code="same_person")
            # Цепочки не растут: объединённые раньше во второй смотрят сразу на основного (resolve идёт не дальше 10 шагов).
            await uow.session.execute(update(Person).where(Person.merged_into == other.id).values(merged_into=main.id))
            await uow.session.execute(
                update(WorkPerson).where(WorkPerson.person_id == other.id).values(person_id=main.id, kind=main.kind)
            )
            await uow.session.execute(update(User).where(User.person_id == other.id).values(person_id=main.id))
            for name in [n.name for n in other.names]:
                add_name(main, name)
            other.names = []  # строки второго удаляются как сироты, у основного — новые с теми же именами
            for field in FILLABLE:
                if getattr(main, field) is None:
                    setattr(main, field, getattr(other, field))
            other.merged_into = main.id
            await self.works.refresh_search(uow, await self._work_ids(uow, main.id))
        logger.info(
            "Профили объединены", extra={"audit": True, "main": str(main.id), "from": str(other.id), "by": str(user.id)}
        )
        return await self.card(main.id)
