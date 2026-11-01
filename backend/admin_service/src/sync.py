"""Ночное обновление по Crossref, ORCID и OpenAlex.

python -m admin_service.src.sync — таймером хоста, как citations:
    docker compose exec -T admin_service python -m admin_service.src.sync
1. Работы с DOI: поля, которые пришли из Crossref и не правились руками (источник doi), обновляются —
   номер выпуска и страницы после выхода в печать, исправленное название. Правленное модератором не трогается,
   пустые поля заполняются. Ничего не изменилось — работа не сохраняется.
2. Люди с ORCID: новые статьи из ORCID и OpenAlex становятся черновиками, как по кнопке «Импорт из ORCID».
Источник не ответил — команда идёт дальше и в конце пишет итог в лог.
"""

import asyncio
from uuid import UUID

from sqlalchemy import select

from admin_service.src.service import doi as crossref
from admin_service.src.service.works_service import DOI_FIELDS, WorkService
from shared.exceptions import DomainError
from shared.logging import get_logger, setup_logging
from shared.people import Person
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_write_factory
from shared.works import Work

logger = get_logger("sync")


def service() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), None)  # файлы команде не нужны


async def refresh_dois(s: WorkService) -> dict[str, int]:
    """→ {works, updated, errors}."""
    async with uow_write_factory() as uow:
        rows = (
            await uow.session.execute(
                select(Work.id, Work.doi).where(
                    Work.type == "publication", Work.doi.is_not(None), Work.status != "rejected"
                )
            )
        ).all()
    try:
        found = await crossref.lookup_many(list(dict.fromkeys(doi.lower() for _, doi in rows)))
    except DomainError:  # Crossref лежит: причина в логе, ORCID-часть ещё может пройти
        return {"works": len(rows), "updated": 0, "errors": 1}
    updated = errors = 0
    for work_id, doi in rows:
        if (incoming := found.get(doi.lower())) is None:
            continue
        try:
            async with uow_write_factory() as uow:
                w = await s._load(uow, work_id)
                doi_fields = [k for k in DOI_FIELDS if (w.field_meta.get(k) or {}).get("source") == "doi"]
                changed, _, _ = await s._apply_doi(uow, w, doi, dict(incoming), doi_fields)
        except Exception:  # одна работа не роняет остальные
            logger.exception("Работа не обновлена по Crossref", extra={"work_id": str(work_id)})
            errors += 1
            continue
        if changed:
            updated += 1
            logger.info(
                "Работа обновлена по Crossref", extra={"audit": True, "work_id": str(work_id), "fields": changed}
            )
    return {"works": len(rows), "updated": updated, "errors": errors}


async def import_people(s: WorkService) -> dict[str, int]:
    """→ {people, drafts, errors}."""
    async with uow_write_factory() as uow:
        ids: list[UUID] = list(
            await uow.session.scalars(select(Person.id).where(Person.orcid.is_not(None), Person.merged_into.is_(None)))
        )
    drafts = errors = 0
    for person_id in ids:
        try:
            drafts += (await s.import_orcid(person_id, None))["created"]
        except DomainError:  # ORCID или Crossref не ответил, профиль не найден
            errors += 1
    return {"people": len(ids), "drafts": drafts, "errors": errors}


async def main() -> None:
    s = service()
    works = await refresh_dois(s)
    people = await import_people(s)
    logger.info("Ночное обновление", extra={"works": works, "people": people})
    print(
        f"Работ с DOI: {works['works']}, обновлено: {works['updated']}; "
        f"людей с ORCID: {people['people']}, новых черновиков: {people['drafts']}; "
        f"ошибок: {works['errors'] + people['errors']}"
    )


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
