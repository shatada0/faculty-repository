"""extraction_worker: регулярки, confidence, тип и уровень по титульнику, стадия ошибки в логе."""

import json
from pathlib import Path

import httpx
import pytest

from extraction_worker.src.service.extract import (
    ItemError,
    _confidence,
    ask_llm,
    by_regex,
    clean_annotation,
    extract,
    norm,
    work_kind,
)


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


def test_profile_only_from_its_label() -> None:
    """Профиль по подписи, с переносом строки; нет подписи — нет профиля (раньше LLM подставляла направление)."""
    page = "Направление: 09.03.01 — Информатика\nНаправленность (профиль): Информационные системы и технологии в\nбизнесе\nМосква — 2021"
    assert by_regex(page, "")["profile"] == "Информационные системы и технологии в бизнесе"
    assert by_regex("Магистерская программа: Инженерия программного обеспечения\n", "")["profile"] == (
        "Инженерия программного обеспечения"
    )
    assert by_regex("Специализация: Математические методы защиты информации", "")["profile"] == (
        "Математические методы защиты информации"
    )
    assert "profile" not in by_regex("Направление: 10.05.01 — Компьютерная безопасность\nМосква 2026", "")


def test_keywords_hyphen_break_and_caps_over_lines() -> None:
    hyphen = "Ключевые слова: кластеризация, электроэнергия, k-\nmeans.\nОбъектом исследования"
    assert by_regex("", hyphen)["keywords"] == ["кластеризация", "электроэнергия", "k-means"]
    caps = "показало свою эффективность.\nИГРОВОЙ ДВИЖОК, КОМПЬЮТЕРНАЯ ГРАФИКА, C++, РЕДАКТОР\nУРОВНЕЙ\n\nВВЕДЕНИЕ"
    assert by_regex("", caps)["keywords"] == ["игровой движок", "компьютерная графика", "c++", "редактор уровней"]


async def test_llm_request_turns_reasoning_off_and_requires_fields() -> None:
    """Ollama не понимает chat_template_kwargs: без reasoning_effort Qwen3.5 тратит все токены на рассуждения."""
    sent: dict = {}

    def answer(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "Тема"}'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        card = await ask_llm(http, "текст", {})
    assert card.title == "Тема" and card.authors == []  # пропуски разбор прощает
    assert sent["reasoning_effort"] == "none" and sent["chat_template_kwargs"] == {"enable_thinking": False}
    schema = sent["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == {"title", "authors", "supervisors", "department", "annotation"}


def test_annotation_without_volume_line() -> None:
    raw = (
        "Выпускная квалификационная работа содержит 83 с., 12 рис., 7 табл., 35 источников. "
        "Объектом исследования является процесс бронирования. Цель работы — исключить пересечения."
    )
    assert (
        clean_annotation(raw)
        == "Объектом исследования является процесс бронирования. Цель работы — исключить пересечения."
    )
    assert clean_annotation("Ключевые слова: а, б. Работа о графах.") == "Работа о графах."
    assert clean_annotation("Работа содержит 50 с.") is None


def test_annotation_confidence_tolerates_grammar_fixes() -> None:
    text = norm(
        "Объектом исследования является отзывы покупателей интернет-магазинов. Цель работы — классифицировать тональность."
    )
    fixed = "Объектом исследования являются отзывы покупателей интернет-магазинов. Цель работы — классифицировать тональность."
    assert _confidence(fixed, text, loose=True) == 0.85
    assert _confidence("Работа о квантовых компьютерах и блокчейне на Марсе.", text, loose=True) == 0.4
