"""catalog_service: ГОСТ, view карточки, выдача, поиск, фасеты, люди — на демо-данных."""

import uuid

import pytest

from admin_service.src.service.works_service import WorkService
from catalog_service.src.service.catalog_service import CatalogService
from catalog_service.src.service.gost import gost_citation
from catalog_service.src.service.view import work_view
from shared.exceptions import NotFoundError
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_ro_factory, uow_write_factory
from tests.fakes import FakeStorage

PUB = {
    "id": "x",
    "type": "publication",
    "year": 2024,
    "keywords": [],
    "language": "ru",
    "accessLevel": "card_only",
    "hasFile": False,
    "indexing": [],
}


def catalog() -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), FakeStorage())  # как в api.py


def works() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage())


def page(**params: str) -> dict:
    """Фильтры фасетов как из строки запроса."""
    return {k: [v] for k, v in params.items()}


def test_gost_journal_article() -> None:
    w = PUB | {
        "kind": "journal_article",
        "title": "Название статьи",
        "venue": "Журнал",
        "volume": "12",
        "issue": "3",
        "pages": "15–24",
        "doi": "10.1/x",
        "authors": [{"displayName": "Иванов И. И."}, {"displayName": "Петров П. П."}],
    }
    assert gost_citation(w) == (
        "Иванов, И. И. Название статьи / И. И. Иванов, П. П. Петров // Журнал. – 2024. – Т. 12, № 3. "
        "– С. 15–24. – DOI 10.1/x."
    )


def test_gost_five_authors() -> None:
    authors = [{"displayName": n} for n in ("А. А. Аа", "Б. Б. Бб", "В. В. Вв", "Г. Г. Гг", "Д. Д. Дд")]
    assert gost_citation(PUB | {"kind": "journal_article", "title": "T", "venue": "J", "authors": authors}).startswith(
        "T / А. А. Аа, Б. Б. Бб, В. В. Вв [и др.] // J"
    )


def test_talk_view() -> None:
    talk = {
        "type": "talk",
        "title": "Доклад",
        "kind": "section",
        "format": "offline",
        "award": None,
        "accessLevel": "full",
        "hasFile": True,
        "year": 2024,
        "language": "ru",
        "authors": [{"displayName": "Иванов И. И.", "kind": "external", "personId": "1"}],
        "presenters": [{"displayName": "Иванов И. И.", "kind": "staff", "personId": "1"}],
        "event": {
            "name": "Конф",
            "level": "national",
            "dateStart": "2024-05-10",
            "dateEnd": "2024-05-11",
            "city": "Самара",
        },
    }
    v = work_view(talk, [])
    assert v["kind"] == "Доклад (секционный)"
    assert v["byline"][0] == {"label": "Докладчики:", "people": [{"name": "Иванов И. И.", "profileId": "1"}]}
    assert v["authors"] == [{"name": "Иванов И. И."}]  # у внешнего автора страницы нет
    assert v["fileButton"] == "Открыть материалы"


async def test_catalog_shows_only_published_visible(demo: dict) -> None:
    c = catalog()
    scopus = await c.catalog(
        page(indexing="scopus"), q=None, type_="publication", exclude=None, sort=None, page=1, page_size=100
    )
    assert scopus["total"] > 0
    assert all(w["type"] == "publication" and "scopus" in w["indexing"] for w in scopus["items"])
    total = (await c.catalog({}, q=None, type_=None, exclude=None, sort=None, page=1, page_size=1))["total"]
    facets = await c.facets({}, q=None, type_=None)
    assert sum(f["count"] for f in facets["type"]) == total
    seen = set()
    for p in range(1, 6):
        res = await c.catalog({}, q=None, type_=None, exclude=None, sort=None, page=p, page_size=100)
        seen |= {w["id"] for w in res["items"]}
    assert len(seen) == total
    ids = demo["ids"]
    assert str(ids["vkr-14"]) not in seen  # скрыта
    assert str(ids["cw-7"]) not in seen  # отклонена
    with pytest.raises(NotFoundError):
        await c.work(ids["cw-7"])


async def test_drafts_are_invisible_publicly(demo: dict) -> None:
    c = catalog()
    drafts = [w for w in demo["data"]["works"] if w["status"] == "draft"]
    for w in drafts[:5]:
        work_id = demo["ids"][w["id"]]
        for call in (c.work, c.file, c.citation):
            with pytest.raises(NotFoundError):
                await call(work_id)


async def test_search_morphology_and_facets(demo: dict) -> None:
    c = catalog()
    res = await c.catalog({}, q="нейронные сети", type_=None, exclude=None, sort=None, page=1, page_size=20)
    assert res["total"] > 0
    lists = await c.catalog(
        page(journalList="white1"), q=None, type_="publication", exclude=None, sort=None, page=1, page_size=100
    )
    assert lists["total"] > 0
    assert all("Белый список, уровень 1" in w["journalLists"] for w in lists["items"])
    facets = await c.facets(page(year="1999"), q=None, type_="publication")
    assert {"value": "1999", "label": "1999", "count": 0} in facets["year"]  # выбранное без совпадений остаётся
    assert facets["grnti"] == []  # счётчики соседних фасетов — по выдаче с выбранным годом
    facets = await c.facets({}, q=None, type_="publication")
    assert any(f["label"].startswith("28.23 ") for f in facets["grnti"])


async def test_home_and_people(demo: dict) -> None:
    c = catalog()
    assert len({w["type"] for w in await c.latest()}) == 4
    orlov = demo["ids"]["p-s1"]
    staff = await c.staff(q="Орлов Д. С.", departments=[], sort=None)
    assert [p["id"] for p in staff["items"]] == [str(orlov)]
    metrics = await c.metrics(orlov, None)
    h, cites = (int(t["value"]) for t in metrics["items"])
    assert 0 < h <= cites
    one = await c.metrics(orlov, str(metrics["years"][0]))
    assert int(one["items"][1]["value"]) <= cites
    graduate = next(p for p in demo["data"]["people"] if p["kind"] == "graduate")
    profile = await c.profile(demo["ids"][graduate["id"]])
    assert profile["tabs"][0]["query"] == {"type": "vkr", "role": "author"} and "Выпускник" in profile["headline"]
    talk = (await c.catalog({}, q=None, type_="talk", exclude=None, sort=None, page=1, page_size=1))["items"][0]
    assert talk["view"]["kind"].startswith("Доклад (") and talk["view"]["byline"][0]["label"] == "Докладчики:"
    assert (await c.citation(uuid.UUID(talk["id"])))["gost"]
    # у кафедры на главной столько же работ, сколько в фасете «Кафедра» каталога
    facet = {f["value"]: f["count"] for f in (await c.facets({}, q=None, type_=None))["department"]}
    assert [d["workCount"] for d in (await c.departments())["items"]] == [
        facet.get(d["name"], 0) for d in (await c.departments())["items"]
    ]


async def test_live_search_prefix_and_typos(demo: dict) -> None:
    c = catalog()

    async def titles(q: str) -> list[str]:
        res = await c.catalog({}, q=q, type_=None, exclude=None, sort=None, page=1, page_size=100)
        return [w["title"].lower() for w in res["items"]]

    exact = await titles("нейронные")
    assert exact
    for q in ("нейр", "нейроные", "нецронных"):  # недописанное слово и опечатки; «нецронных» — только триграммы
        assert any("нейрон" in t for t in await titles(q)), q
    assert set(await titles("нейр")) >= set(exact)
    assert not any("граф" in t for t in await titles("нейр -графовых"))  # минус работает, похожие не подмешаны
    facets = await c.facets({}, q="нецронных", type_=None)
    assert sum(f["count"] for f in facets["type"]) == len(await titles("нецронных"))  # фасеты ищут так же


async def test_suggest_works_and_people(demo: dict) -> None:
    c = catalog()
    assert await c.suggest("  ") == {"works": [], "people": []}
    res = await c.suggest("нейр")
    assert 0 < len(res["works"]) <= 6
    hits = [p["text"].lower() for w in res["works"] for p in w["parts"] if p["hit"]]
    assert hits and all(h.startswith("нейр") for h in hits)
    assert all("".join(p["text"] for p in w["parts"]) == w["title"] for w in res["works"])
    orlov = str(demo["ids"]["p-s1"])
    assert [p["id"] for p in (await c.suggest("Орлов"))["people"]] == [orlov]
    assert orlov in [p["id"] for p in (await c.suggest("Орлов Дмитрй"))["people"]]  # опечатка в имени
    hidden = [w for w in demo["data"]["works"] if w["status"] != "published" or w.get("accessLevel") == "hidden"]
    for w in hidden[:5]:
        found = [x["id"] for x in (await c.suggest(w["title"]))["works"]]
        assert str(demo["ids"][w["id"]]) not in found
