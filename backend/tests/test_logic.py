"""Логика без базы и брокера: перенос src/mocks/logic.test.ts и проверки безопасности из спека."""

import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import ValidationError

from admin_service import api as admin_api
from admin_service.main import app as admin_app
from auth_service.main import app as auth_app
from catalog_service.gost import gost_citation
from catalog_service.view import work_view
from extraction_worker.extract import ItemError, _confidence, by_regex, extract, norm, work_kind
from import_service import archive, records
from import_service import main as import_main
from import_service.main import app as import_app
from shared import labels as L
from shared.auth import SessionUser, _key, issue_token, read_token, require_author, require_moderator
from shared.checks import errors, unsure
from shared.names import name_query, parse_name, short_key, short_name
from shared.tables import csv_line, missing_columns, parse_csv, to_records
from shared.works import parse_write

# ---------- ФИО, подписи, таблицы ----------


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


# ---------- ГОСТ и карточка ----------

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


# ---------- проверки черновика и схемы записи ----------

TALK = {
    "type": "talk",
    "title": "Доклад",
    "authors": [{"displayName": "Иванов И. И.", "kind": "staff"}],
    "presenters": [{"displayName": "Иванов И. И.", "kind": "staff"}],
    "year": 2024,
    "kind": "section",
    "event": {"name": "Конф", "level": "national", "dateStart": "2024-05-10", "dateEnd": "2024-05-09"},
}


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


# ---------- импорт ----------


def test_table_rows_to_drafts() -> None:
    fields, meta, authors = records.publication(
        {
            "Название": "Paper",
            "Авторы": "Иванов И. И.; Петров П. П.",
            "Год": "2024",
            "Вид": "Статья в журнале",
            "Журнал/сборник": "J",
            "ISSN/ISBN": "ISBN 978-5-1",
            "Индексация": "Scopus, ВАК",
        }
    )
    assert authors == ["Иванов И. И.", "Петров П. П."]
    assert fields["language"] == "en" and fields["kind"] == "journal_article" and fields["isbn"] == "978-5-1"
    assert fields["indexing"] == ["scopus", "vak"] and fields["issn"] is None
    assert meta["title"] == {"source": "table"} and "volume" not in meta
    fields, _, presenters, _ = records.talk(
        {
            "Название доклада": "Доклад",
            "Докладчики": "А",
            "Конференция": "К",
            "Уровень": "международный",
            "Дата начала": "12.05.2024",
        }
    )
    assert presenters == ["А"]
    assert (
        fields["event"]["dateStart"] == "2024-05-12"
        and fields["year"] == 2024
        and fields["event"]["level"] == "international"
    )
    assert records.row_error(["Год", "Название"]) == "Не указан год"


def _zip(files: dict[str, bytes], symlink: str | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    return buf


def test_zip_only_pdfs_no_paths_no_links(tmp_path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(
        _zip({"../../evil/Иванов_ВКР.pdf": b"%PDF-1", "notes.txt": b"x", "inner.zip": b"PK"}, "link.pdf").getvalue()
    )
    out = archive.extract_pdfs(path, tmp_path)
    assert [name for name, _ in out] == ["Иванов_ВКР.pdf"]
    assert out[0][1] == tmp_path / "1.pdf"  # имя из архива в путь не попало


def test_zip_limits(tmp_path, monkeypatch) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_zip({f"{i}.pdf": b"%PDF-1" for i in range(3)}).getvalue())
    monkeypatch.setattr(archive, "MAX_FILES", 2)
    with pytest.raises(archive.ArchiveError):
        archive.extract_pdfs(path, tmp_path)
    monkeypatch.setattr(archive, "MAX_FILES", 10)
    monkeypatch.setattr(archive, "MAX_TOTAL", 10)
    with pytest.raises(archive.ArchiveError, match="2 ГБ"):
        archive.extract_pdfs(path, tmp_path)


def test_regex_and_confidence() -> None:
    title = "Кафедра информационных систем\nВыпускная квалификационная работа бакалавра\n09.03.02\nМосква 2026"
    text = title + "\nРеферат\nРабота содержит 68 с.\nКлючевые слова: нейронные сети, ГРАФЫ.\n"
    got = by_regex(title, text)
    assert got == {
        "directionCode": "09.03.02",
        "year": 2026,
        "pages": 68,
        "keywords": ["нейронные сети", "графы"],
        "level": "bachelor",
    }
    normalized = norm("Руководитель: Белова Наталья Викторовна")
    assert _confidence(["Н. В. Белова"], normalized, name=True) == 0.85
    assert _confidence("Выдуманная тема", normalized) == 0.4


# ---------- авторизация и мидлвари ----------

USER = SessionUser(
    id=__import__("uuid").uuid4(), name="Модератор", role="moderator", person_id=None, is_active=True, token_version=0
)


def test_tokens_eddsa_only() -> None:
    assert read_token(issue_token(USER))["sub"] == str(USER.id)
    now = datetime.now(UTC)
    claims = {"sub": str(USER.id), "ver": 0, "exp": now + timedelta(hours=1)}
    assert read_token(jwt.encode(claims, key=None, algorithm="none")) is None
    assert read_token(jwt.encode(claims, "s" * 32, algorithm="HS256")) is None
    assert (
        read_token(jwt.encode(claims | {"exp": now - timedelta(seconds=1)}, _key("jwt_private"), algorithm="EdDSA"))
        is None
    )
    assert read_token(issue_token(USER)[:-4] + "AAAA") is None


def _deps(d: Dependant):
    for s in d.dependencies:
        yield s.call
        yield from _deps(s)


@pytest.mark.parametrize(
    ("router", "guard"),
    [(admin_api.admin, require_moderator), (import_main.router, require_moderator), (admin_api.my, require_author)],
)
def test_routes_need_role(router, guard) -> None:
    """Роутер без зависимости открыл бы /api/admin/* или /api/my/* всем."""
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert routes
    for r in routes:
        assert guard in _deps(r.dependant), r.path


@pytest.mark.parametrize("app", [admin_app, import_app])
async def test_anonymous_gets_401_everywhere(app) -> None:
    routers = [admin_api.admin, admin_api.my] if app is admin_app else [import_main.router]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        for router in routers:
            for r in router.routes:
                path = re.sub(r"\{[^}]+\}", "0190a0a0-0000-7000-8000-000000000000", r.path).replace(
                    "{work_type}", "vkr"
                )
                for method in r.methods:
                    response = await client.request(method, path)
                    assert response.status_code == 401, (method, r.path)


async def _post(app, path: str, headers: dict, json: object = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.post(path, headers=headers, json=json)


async def test_origin_check_blocks_foreign_sites() -> None:
    r = await _post(auth_app, "/api/auth/logout", {"origin": "https://student.example.ru"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    r = await _post(auth_app, "/api/auth/logout", {"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert (await _post(auth_app, "/api/auth/logout", {"origin": "https://localhost"})).status_code == 204
    assert (await _post(auth_app, "/api/auth/logout", {})).status_code == 204  # не браузер: curl, тесты


async def test_errors_have_one_format_and_trace_id() -> None:
    r = await _post(auth_app, "/api/auth/login", {"x-request-id": "abc"}, {"login": "x"})
    assert r.status_code == 422 and r.headers["x-request-id"] == "abc"
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["fields"] == [{"field": "password", "message": "Field required"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://t") as client:
        r = await client.get("/api/admin/summary")
    assert r.status_code == 401 and r.json() == {"error": {"code": "unauthorized", "message": "Нужен вход"}}


async def test_worker_log_names_failed_stage() -> None:
    """Итоговая строка воркера берёт стадию из log: по ней дашборд раскладывает ошибки."""
    pdf = (Path(__file__).parent.parent / "fixtures" / "sample.pdf").read_bytes()
    bad_json = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]}))
    async with httpx.AsyncClient(transport=bad_json) as http:
        for data, stage in ((b"not a pdf", "pdf"), (pdf, "parse")):
            log: dict = {}
            with pytest.raises(ItemError):
                await extract(data, http, log)
            assert log["stage"] == stage


def test_work_kind_from_title_page() -> None:
    assert work_kind("ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА БАКАЛАВРА") == {"workType": "vkr"}
    assert work_kind("Курсовая работа по дисциплине «Базы данных»") == {
        "workType": "coursework",
        "courseworkKind": "work",
    }
    assert work_kind("КУРСОВОЙ ПРОЕКТ") == {"workType": "coursework", "courseworkKind": "project"}
    assert work_kind("Дипломная работа") == {"workType": "vkr"}
    assert work_kind("Отчёт о практике") == {}


def test_level_from_direction_code() -> None:
    assert by_regex("КУРСОВОЙ ПРОЕКТ\n09.04.01 Информатика\nМосква 2026", "")["level"] == "master"
    assert by_regex("Курсовая работа\n10.05.01 Компьютерная безопасность", "")["level"] == "specialist"
    assert by_regex("Выпускная работа магистра\n09.03.04", "")["level"] == "master"  # слово важнее кода
