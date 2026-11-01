"""Цитирования по DOI: Crossref (is-referenced-by-count) и OpenAlex (cited_by_count) → work_citations.

python -m admin_service.src.citations — ночью таймером хоста, как бэкап:
    docker compose exec -T admin_service python -m admin_service.src.citations
В API citations — максимум по источникам; по нему считается индекс Хирша на странице человека.
Источник не ответил — его прежние числа остаются, команда идёт дальше и в конце пишет итог в лог.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select

from shared.config import app_config
from shared.exceptions import UpstreamError
from shared.logging import get_logger, setup_logging
from shared.repository.works_repository import WorkRepository
from shared.resilience import guard
from shared.uow import uow_ro_factory, uow_write_factory
from shared.works import Work

logger = get_logger("citations")
BATCH = 50  # столько DOI берут фильтры Crossref и OpenAlex одним запросом
CROSSREF = "https://api.crossref.org/works"
OPENALEX = "https://api.openalex.org/works"

Fetch = Callable[[httpx.AsyncClient, list[str]], Awaitable[dict[str, int]]]


def _batchable(doi: str) -> bool:
    """«,» и «|» — разделители фильтров: такие DOI (старые SICI у Wiley) спрашиваются по одному."""
    return "," not in doi and "|" not in doi


async def crossref(http: httpx.AsyncClient, dois: list[str]) -> dict[str, int]:
    if len(dois) == 1 and not _batchable(dois[0]):
        r = await guard("Crossref").request(http, "GET", f"{CROSSREF}/{quote(dois[0], safe='')}")
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        m = r.json()["message"]
        return {m["DOI"].lower(): m.get("is-referenced-by-count", 0)}
    params = {"filter": ",".join(f"doi:{d}" for d in dois), "rows": len(dois), "select": "DOI,is-referenced-by-count"}
    r = await guard("Crossref").request(http, "GET", CROSSREF, params=params)
    r.raise_for_status()
    return {i["DOI"].lower(): i.get("is-referenced-by-count", 0) for i in r.json()["message"]["items"]}


async def openalex(http: httpx.AsyncClient, dois: list[str]) -> dict[str, int]:
    mailto = {"mailto": app_config.crossref_mailto} if app_config.crossref_mailto else {}
    if app_config.openalex_api_key:
        mailto["api_key"] = app_config.openalex_api_key
    if len(dois) == 1 and not _batchable(dois[0]):
        r = await guard("OpenAlex").request(http, "GET", f"{OPENALEX}/doi:{quote(dois[0], safe='')}", params=mailto)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return {dois[0]: r.json().get("cited_by_count", 0)}
    params = {"filter": "doi:" + "|".join(dois), "per-page": len(dois), "select": "doi,cited_by_count", **mailto}
    r = await guard("OpenAlex").request(http, "GET", OPENALEX, params=params)
    r.raise_for_status()
    return {
        i["doi"].lower().removeprefix("https://doi.org/"): i.get("cited_by_count", 0)
        for i in r.json()["results"]
        if i.get("doi")
    }


SOURCES: dict[str, Fetch] = {"crossref": crossref, "openalex": openalex}


def batches(dois: list[str]) -> list[list[str]]:
    plain = [d for d in dois if _batchable(d)]
    return [plain[i : i + BATCH] for i in range(0, len(plain), BATCH)] + [[d] for d in dois if not _batchable(d)]


async def update(http: httpx.AsyncClient, sources: dict[str, Fetch] = SOURCES) -> dict[str, int]:
    """Все публикации с DOI, кроме отклонённых. → {works, rows, errors}."""
    async with uow_ro_factory() as uow:
        rows = (
            await uow.session.execute(
                select(Work.id, Work.doi).where(
                    Work.type == "publication", Work.doi.is_not(None), Work.status != "rejected"
                )
            )
        ).all()
    by_doi: dict[str, list[UUID]] = defaultdict(list)  # дубль до слияния — та же статья у двух работ
    for work_id, doi in rows:
        by_doi[doi.lower()].append(work_id)
    works = WorkRepository()
    saved = errors = 0
    for batch in batches(list(by_doi)):
        found: list[tuple[UUID, str, int]] = []
        for source, fetch in sources.items():
            try:
                counts = await fetch(http, batch)
            except (httpx.HTTPError, ValueError, KeyError, UpstreamError) as e:  # UpstreamError — цепь разомкнута
                errors += 1
                logger.warning("Источник цитирований не ответил", extra={"source": source, "error": repr(e)})
                continue
            found += [(w, source, n) for doi, n in counts.items() for w in by_doi.get(doi, [])]
        async with uow_write_factory() as uow:
            saved += await works.save_citations(uow, found)
    stats = {"works": len(rows), "rows": saved, "errors": errors}
    logger.info("Цитирования обновлены", extra=stats)
    return stats


async def main() -> None:
    agent = "faculty-repository" + (f" (mailto:{app_config.crossref_mailto})" if app_config.crossref_mailto else "")
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": agent}) as http:
        stats = await update(http)
    print(f"Работ с DOI: {stats['works']}, записано чисел: {stats['rows']}, ошибок источников: {stats['errors']}")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
