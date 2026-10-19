"""shared: ФИО, подписи, таблицы, проверки и схемы записи работ, роли базы."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from admin_service.src.service.works_service import WorkService
from catalog_service.src.service.catalog_service import CatalogService
from shared.imports.tables import csv_line, missing_columns, parse_csv, to_records
from shared.people import Person
from shared.people.names import name_query, parse_name, short_key, short_name, surname_first
from shared.repository.people_repository import PersonRepository
from shared.repository.works_repository import WorkRepository
from shared.uow import uow_ro_factory, uow_write_factory
from shared.works import labels as L
from shared.works.checks import errors, unsure
from shared.works.schemas import parse_write
from tests.fakes import FakeStorage

TALK = {
    "type": "talk",
    "title": "Доклад",
    "authors": [{"displayName": "Иванов И. И.", "kind": "staff"}],
    "presenters": [{"displayName": "Иванов И. И.", "kind": "staff"}],
    "year": 2024,
    "kind": "section",
    "event": {"name": "Конф", "level": "national", "dateStart": "2024-05-10", "dateEnd": "2024-05-09"},
}


def catalog() -> CatalogService:
    return CatalogService(uow_ro_factory, WorkRepository(), PersonRepository(), FakeStorage())  # как в api.py


def works() -> WorkService:
    return WorkService(uow_write_factory, WorkRepository(), PersonRepository(), FakeStorage())


def test_names_read_every_common_spelling() -> None:
    assert parse_name("Иванов Иван Иванович") == ("Иванов", ["И", "И"])
    assert parse_name("И.И. Иванов") == ("Иванов", ["И", "И"])
    assert short_name("Белова Наталья Викторовна") == "Белова Н. В."
    assert short_key("Иванов И.И.") == short_key("I. Ivanov")
    assert short_key("Захаров И.") == short_key("Zakharov I.")
    assert name_query("Иванов И. И.") == "иванов:* & и:* & и:*"
    assert name_query("' | ! :*") is None  # синтаксис tsquery не сломать


def test_labels_with_endings() -> None:
    assert L.from_label(L.CONF_LEVEL, "международный") == "international"
    assert L.from_label(L.PUB_KIND, "Статья в сборнике") == "proceedings_paper"
    assert L.from_label(L.PUB_KIND, "Статья") is None  # неоднозначная основа
    assert [L.plural(n, L.FORMS["publication"]) for n in (1, 3, 5, 11, 21, 112)] == [
        "публикация",
        "публикации",
        "публикаций",
        "публикаций",
        "публикация",
        "публикаций",
    ]
    assert L.format_date_range("2024-05-12", "2024-05-14") == "12–14 мая 2024 г."
    assert L.format_date_range("2024-05-30", "2024-06-02") == "30 мая – 2 июня 2024 г."
    assert L.format_size(2_400_000) == "2.3 МБ"


def test_csv_delimiter_quotes_bom_crlf() -> None:
    text = "\N{BYTE ORDER MARK}Авторы;Название\r\n" + '"Иванов И. И.; Петров П. П.";"Статья ""в кавычках"""\r\n'
    assert parse_csv(text) == [["Авторы", "Название"], ["Иванов И. И.; Петров П. П.", 'Статья "в кавычках"']]
    assert missing_columns(["Авторы", "Название"], "publication") == ["Вид", "Журнал/сборник", "Год"]
    record = to_records([["Год", "Название"], ["2024", "X"]], "publication")[0]
    assert (record["Год"], record["Название"], record["Авторы"]) == ("2024", "X", "")


def test_export_cells_are_not_formulas() -> None:
    assert csv_line(["=HYPERLINK(1)", "ok", 5]) == "'=HYPERLINK(1);ok;5\r\n"


def test_talk_end_date_cannot_precede_start() -> None:
    assert errors(TALK) == [{"field": "event.dateEnd", "message": "Дата окончания не раньше даты начала"}]


def test_errors_carry_dotted_paths() -> None:
    year = datetime.now(UTC).year + 2
    errs = errors(TALK | {"authors": [{"displayName": ""}], "year": year, "event": TALK["event"] | {"dateEnd": None}})
    assert errs == [
        {"field": "authors.0.displayName", "message": "Укажите ФИО"},
        {"field": "year", "message": f"Год не позже {year - 1}"},
    ]


def test_student_full_text_needs_consent() -> None:
    vkr = {
        "type": "vkr",
        "title": "Т",
        "authors": [{"displayName": "А"}],
        "supervisors": [{"displayName": "Б"}],
        "year": 2024,
        "level": "bachelor",
        "direction": {"code": "09.03.02"},
        "department": "Кафедра",
        "consent": False,
        "accessLevel": "full",
    }
    assert errors(vkr) == [{"field": "accessLevel", "message": "Нет согласия студента на открытый доступ к тексту"}]
    assert errors(vkr | {"consent": True}) == []


def test_book_needs_publisher_article_needs_venue() -> None:
    base = {
        "type": "publication",
        "title": "Т",
        "authors": [{"displayName": "А"}],
        "year": 2024,
        "venue": None,
        "publisher": None,
    }
    assert [e["field"] for e in errors(base | {"kind": "monograph"})] == ["publisher"]
    assert [e["field"] for e in errors(base | {"kind": "journal_article"})] == ["venue"]


def test_write_schema_blanks_forbids_and_urls() -> None:
    model = parse_write("publication", {"title": "", "doi": "", "id": "ignored", "errors": [1], "relatedTalkId": ""})
    assert model.title is None and model.doi is None and model.relatedTalkId is None
    with pytest.raises(ValidationError):
        parse_write("publication", {"unknown": 1})  # mass assignment
    with pytest.raises(ValidationError):
        parse_write("publication", {"externalUrl": "javascript:alert(1)"})
    assert parse_write("talk", {"event": {"url": "https://conf.example.ru/x"}}).event.url == "https://conf.example.ru/x"


def test_unsure_ai_meta() -> None:
    assert unsure({"source": "ai", "confidence": 0.5})
    assert not unsure({"source": "ai", "confidence": 0.9}) and not unsure({"source": "table"})


async def test_db_roles_have_only_their_rights(demo: dict) -> None:
    async with uow_write_factory() as uow:
        await uow.session.execute(text("SET LOCAL ROLE writer"))
        await uow.session.execute(text("SELECT id, role, token_version FROM users"))
        with pytest.raises(DBAPIError, match="permission denied"):
            async with uow.session.begin_nested():
                await uow.session.execute(text("SELECT password_hash FROM users"))
    for role, sql in (
        ("catalog", "SELECT id FROM users"),
        ("catalog", "UPDATE works SET title = title"),
        ("auth", "SELECT id FROM works"),
        ("grafana_ro", "SELECT id FROM works"),
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with uow_write_factory() as uow:
                await uow.session.execute(text(f"SET LOCAL ROLE {role}"))
                await uow.session.execute(text(sql))
    async with uow_write_factory() as uow:
        await uow.session.execute(text("SET LOCAL ROLE grafana_ro"))
        assert (await uow.session.execute(text("SELECT sum(works) FROM stats.works_total"))).scalar() > 0
    async with uow_write_factory() as uow:
        assert await uow.session.get(Person, demo["ids"]["p-s1"])


def test_initials_before_surname_read_as_usual_order() -> None:
    assert surname_first("Р. И. Степанов") == "Степанов Р. И."
    assert surname_first("Р.И. Степанов") == "Степанов Р. И."
    assert surname_first("Степанов Роман Игоревич") == "Степанов Роман Игоревич"  # полное ФИО не трогаем
    assert surname_first("Степанов Р. И.") == "Степанов Р. И."
