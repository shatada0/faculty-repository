"""Сервис каталога и фильтры фасетов из строки запроса."""

from collections import defaultdict
from typing import Annotated, Literal

from fastapi import Depends, Query, Request

from catalog_service.src.service.catalog_service import CatalogService, Params
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_ro_factory

WorkType = Literal["vkr", "coursework", "publication", "talk"]
Q = Annotated[str | None, Query(max_length=200)]


def get_service(request: Request) -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), request.app.state.storage)


def filter_params(request: Request) -> Params:
    """Фильтры фасетов — любые повторяющиеся ключи запроса; неизвестные ключи никуда не попадают."""
    out: Params = defaultdict(list)
    for k, v in request.query_params.multi_items():
        out[k].append(v[:200])
    return out


Service = Annotated[CatalogService, Depends(get_service)]
Filters = Annotated[Params, Depends(filter_params)]
