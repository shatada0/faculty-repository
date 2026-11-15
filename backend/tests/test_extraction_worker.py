"""extraction_worker: регулярки, confidence, тип и уровень по титульнику, стадия ошибки в логе."""

import asyncio
import io
import json
import uuid
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from extraction_worker.src.service import extract as extract_module
from extraction_worker.src.service import extraction_service
from extraction_worker.src.service.extract import (
    ItemError,
    LLMUnavailable,
    _confidence,
    ask_llm,
    by_regex,
    clean_annotation,
    dehyphen,
    extract,
    norm,
    pdf_pages,
    work_kind,
)
from extraction_worker.src.service.extraction_service import MAX_PDF_BYTES, MAX_RETRIES, ExtractionHandler
from shared.bus.consumer import Delivery
from shared.bus.contracts import ItemCreatedDTO
from shared.exceptions import RetryMessageError
from tests.fakes import FakePublisher, FakeStorage

SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.pdf"
TITLE = SAMPLE.with_name("vkr-title.pdf")


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
    pdf = SAMPLE.read_bytes()
    bad_json = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]}))
    async with httpx.AsyncClient(transport=bad_json) as http:
        for data, stage in ((b"not a pdf", "pdf"), (pdf, "parse")):
            log: dict = {}
            with pytest.raises(ItemError):
                await extract(data, http, log)
            assert log["stage"] == stage


async def test_scan_goes_through_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет текстового слоя: без Tesseract — ошибка, как раньше; с ним текст распознаётся и идёт в LLM."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    buf = io.BytesIO()
    writer.write(buf)
    scan = buf.getvalue()
    title = await pdf_pages(TITLE.read_bytes())
    seen = []

    async def ocr_pages(data: bytes) -> list[str]:
        seen.append(data)
        return title if len(seen) == 1 else ["   "]

    monkeypatch.setattr(extract_module, "ocr_pages", ocr_pages)
    bad_json = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]}))
    async with httpx.AsyncClient(transport=bad_json) as http:
        monkeypatch.setattr(extract_module, "OCR", False)
        with pytest.raises(ItemError, match="без текстового слоя"):
            await extract(scan, http, {})
        monkeypatch.setattr(extract_module, "OCR", True)
        log: dict = {}
        with pytest.raises(ItemError, match="неверный ответ"):
            await extract(scan, http, log)
        assert log["stage"] == "parse" and seen == [scan]  # распознанный текст дошёл до модели
        with pytest.raises(ItemError, match="не удалось распознать"):
            await extract(scan, http, {})

    seen.clear()
    card = {"title": "Сервис", "authors": [], "supervisors": [], "department": None, "annotation": None}
    ok = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(card)}}]})
    )
    async with httpx.AsyncClient(transport=ok) as http:
        fields, meta = await extract(scan, http, {})
    assert fields["directionCode"] and meta["directionCode"]["confidence"] == extract_module.OCR_CONFIDENCE
    assert all(m["confidence"] <= extract_module.OCR_CONFIDENCE for m in meta.values())  # скан — к модератору


@pytest.mark.skipif(not extract_module.OCR, reason="нужен tesseract с моделью rus")
async def test_tesseract_reads_title_page() -> None:
    pages = await extract_module.ocr_pages(TITLE.read_bytes())
    assert "квалификационная" in norm(pages[0])


def test_work_kind_from_title_page() -> None:
    assert work_kind("ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА БАКАЛАВРА") == {"workType": "vkr"}
    assert work_kind("Курсовая работа по дисциплине «Базы данных»") == {
        "workType": "coursework",
        "courseworkKind": "work",
    }
    assert work_kind("КУРСОВОЙ ПРОЕКТ") == {"workType": "coursework", "courseworkKind": "project"}
    assert work_kind("Дипломная работа") == {"workType": "vkr"}
    assert work_kind("Отчёт о практике") == {}
    # «курсов» в теме ВКР — не курсовая
    assert work_kind("Выпускная квалификационная работа\nПлатформа онлайн-курсов") == {"workType": "vkr"}
    assert work_kind("Прогнозирование курсов валют") == {}


def test_level_from_direction_code() -> None:
    assert by_regex("КУРСОВОЙ ПРОЕКТ\n09.04.01 Информатика\nМосква 2026", "")["level"] == "master"
    assert by_regex("Курсовая работа\n10.05.01 Компьютерная безопасность", "")["level"] == "specialist"
    # код важнее слова: «магистр» бывает в регалиях руководителя
    assert by_regex("Руководитель: доцент, магистр техники\n09.03.04", "")["level"] == "bachelor"
    assert by_regex("Выпускная работа магистра", "")["level"] == "master"


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
    assert by_regex("", dehyphen(hyphen))["keywords"] == ["кластеризация", "электроэнергия", "k-means"]
    caps = "показало свою эффективность.\nИГРОВОЙ ДВИЖОК, КОМПЬЮТЕРНАЯ ГРАФИКА, C++, РЕДАКТОР\nУРОВНЕЙ\n\nВВЕДЕНИЕ"
    assert by_regex("", caps)["keywords"] == ["игровой движок", "компьютерная графика", "c++", "редактор уровней"]


async def test_llm_request_turns_reasoning_off_and_requires_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Ollama не понимает chat_template_kwargs: без reasoning_effort Qwen3.5 тратит все токены на рассуждения."""
    sent: dict = {}

    def answer(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "Тема"}'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        with caplog.at_level("INFO"):
            card = await ask_llm(http, "текст", {})
    assert card.title == "Тема" and card.authors == []  # пропуски разбор прощает
    # по этой строке строятся панели «ИИ» в Grafana: время ответа и исходы запросов к модели
    answered = [r for r in caplog.records if r.getMessage() == "LLM ответил"]
    assert len(answered) == 1 and answered[0].duration_ms >= 0
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


def test_keywords_stop_at_next_sentence_and_skip_title_page() -> None:
    no_dot = "Ключевые слова: нейросети, обучение\nОбъектом исследования являются модели, методы и средства.\n"
    assert by_regex("", no_dot)["keywords"] == ["нейросети", "обучение"]
    after_comma = "Ключевые слова: нейросети,\nPython, Django.\nВВЕДЕНИЕ"
    assert by_regex("", after_comma)["keywords"] == ["нейросети", "python", "django"]
    title = "ИВАНОВ И.И., ПЕТРОВ П.П., СИДОРОВ С.С.\nМосква 2026"
    assert "keywords" not in by_regex(title, title + "\nРеферат\nО графах.")


async def llm_answer(answer) -> object:
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        try:
            return await ask_llm(http, "текст", {})
        except (ItemError, LLMUnavailable) as e:
            return e


def fails(e: Exception):
    def answer(_: httpx.Request) -> httpx.Response:
        raise e

    return answer


async def test_llm_errors_by_kind() -> None:
    """null вместо ответа — ошибка ответа; обрыв соединения — повтор; длинное поле — обрезано, а не вся карточка."""
    empty = await llm_answer(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    assert isinstance(empty, ItemError)
    assert isinstance(await llm_answer(fails(httpx.ReadError("reset"))), LLMUnavailable)
    assert isinstance(await llm_answer(fails(httpx.ConnectTimeout("t"))), LLMUnavailable)
    assert isinstance(await llm_answer(fails(httpx.ReadTimeout("t"))), ItemError)
    long = json.dumps({"title": "т" * 900, "authors": [f"Иванов {i}" for i in range(12)]})
    card = await llm_answer(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": long}}]}))
    assert len(card.title) == 500 and len(card.authors) == 10


async def test_pdf_with_password_and_no_orphan_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(SAMPLE))
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ItemError, match="паролем"):
        await pdf_pages(buf.getvalue())

    procs = []
    spawn = asyncio.create_subprocess_exec

    async def tracked(*args, **kwargs):
        procs.append(await spawn(*args, **kwargs))
        return procs[-1]

    monkeypatch.setattr(extract_module.asyncio, "create_subprocess_exec", tracked)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):  # отмена снаружи, как таймаут элемента: pypdf ещё не закончил
            await pdf_pages(SAMPLE.read_bytes())
    assert procs[0].returncode is not None


def item(storage: FakeStorage, body: bytes = b"%PDF-1.4") -> ItemCreatedDTO:
    storage.objects["imports/a.pdf"] = body
    return ItemCreatedDTO(item_id=uuid4(), file_key="imports/a.pdf", work_type="vkr")


async def test_handler_retries_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_):
        raise LLMUnavailable("down")

    monkeypatch.setattr(extraction_service, "extract", down)
    storage, publisher = FakeStorage(), FakePublisher()
    handler = ExtractionHandler(storage, publisher, None)
    dto = item(storage)
    with pytest.raises(RetryMessageError):
        await handler(dto, Delivery(False, 0, MAX_RETRIES - 1))
    assert publisher.published == []
    await handler(dto, Delivery(False, 0, MAX_RETRIES))
    assert publisher.published[0][1].error == "ИИ не ответил"


async def test_handler_skips_big_and_crashing_files(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*_):
        raise AssertionError("extract не должен вызываться")

    monkeypatch.setattr(extraction_service, "extract", never)
    storage, publisher = FakeStorage(), FakePublisher()
    handler = ExtractionHandler(storage, publisher, None)
    await handler(item(storage, b"%PDF-" + bytes(MAX_PDF_BYTES)), Delivery(False, 0, 0))
    await handler(item(storage), Delivery(True, 2, 0))  # третья доставка: файл ронял воркер
    assert "МБ" in publisher.published[0][1].error
    assert publisher.published[1][1].error == "Файл не удалось разобрать"


async def test_handler_retries_when_result_not_published() -> None:
    """В dead letters элемент остался бы «в очереди» навсегда."""

    class Broken(FakePublisher):
        async def publish(self, topic, dto) -> None:
            raise ConnectionError("channel closed")

    storage = FakeStorage()
    with pytest.raises(RetryMessageError):
        await ExtractionHandler(storage, Broken(), None)(item(storage, b"not a pdf"), Delivery(False, 0, 0))


def test_real_title_pages_year_caps_and_snapped_names() -> None:
    """Формы с настоящих титульников ВКР (DSpace СПбГУ, 30 работ 2025–2026)."""
    from extraction_worker.src.service.extract import by_regex, fix_caps, snap_name

    for tail in ("Санкт-Петербург\n2026 год", "Санкт-Петербург\n2025г.", "Санкт-Петербург\n2026 г .", "Москва 2026"):
        assert by_regex("ОП ВМ.5891.2024 «Робототехника»\n" + tail, "")["year"] in (2025, 2026), tail
    page = "Санкт-Петербургский государственный университет\nГОВОРОВА Диана Игоревна\nВЕЙБЕР Евгения Николаевна\n"
    page += "Научный руководитель:\nд.т.н. Дегтярёв Александр Борисович\n"
    assert fix_caps(snap_name("ГОВОРАВА Диана Игоревна", page)) == "Говорова Диана Игоревна"  # опечатка модели
    assert fix_caps(snap_name("Veiber Evgenia", page)) == "Вейбер Евгения Николаевна"  # латиница вместо кириллицы
    assert snap_name("Дегтярев Александр Борисович", page) == "Дегтярев Александр Борисович"  # «ё» = «е»: уже в тексте
    assert snap_name("Петров Пётр Петрович", page) == "Петров Пётр Петрович"  # чужого в тексте нет — как есть
    assert fix_caps("ГАЙДАЙ-ТУРЛОВ Иван Павлович") == "Гайдай-Турлов Иван Павлович"
    assert fix_caps("Иванов И. И.") == "Иванов И. И." and fix_caps("Ю.В. Литвинов") == "Ю.В. Литвинов"
    # pypdf прочитал «ХРАМОВ» как «ХР АМОВ»: обломок не заменяет верное значение модели
    assert (
        snap_name("Храмов Владислав Александрович", "ХР АМОВ Владислав Александрович")
        == "Храмов Владислав Александрович"
    )
    assert snap_name("R.Sh. Azimov", "Р. Ш. Азимов") == "R.Sh. Azimov"  # инициалы не притягиваем


def test_keywords_list_continues_on_lowercase_line() -> None:
    """Перенос внутри списка — не конец: «градиентный / бустинг» (регрессия: IGNORECASE ловил «б» как заглавную)."""
    from extraction_worker.src.service.extract import by_regex

    text = "\n".join(
        [
            "Ключевые слова: машинное обучение, отток клиентов, градиентный",
            "бустинг, классификация.",
            "Объектом является",
        ]
    )
    assert by_regex("", text)["keywords"] == [
        "машинное обучение",
        "отток клиентов",
        "градиентный бустинг",
        "классификация",
    ]


async def test_text_record_by_llm_with_confidence() -> None:
    answer = {
        "authors": ["Орлов Д. С.", "Выдуманный А. А."],
        "title": "Графовые сети",
        "kind": "Статья в журнале",
        "venue": "Вестник",
        "year": "2025",
        "volume": None,
        "issue": "1",
        "pages": "1–9",
        "publisher": None,
        "doi": None,
        "issn_isbn": None,
    }
    seen = {}

    def reply(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]})

    text = "Орлов Д. С. Графовые сети // Вестник. 2025. № 1. С. 1–9."
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        fields, meta = await extract_module.extract_record(text, "publication", http, {})
    schema = seen["body"]["response_format"]["json_schema"]["schema"]
    assert set(schema["required"]) == set(answer)  # все поля обязательны, как у карточки ВКР
    assert fields["record"] == {
        "Авторы": "Орлов Д. С.; Выдуманный А. А.",
        "Название": "Графовые сети",
        "Вид": "Статья в журнале",
        "Журнал/сборник": "Вестник",
        "Год": "2025",
        "Номер": "1",
        "Страницы": "1–9",
    }
    assert meta["Название"]["confidence"] == extract_module.IN_TEXT
    assert meta["Авторы"]["confidence"] == extract_module.GUESSED  # второго автора в записи нет
    assert meta["Вид"]["confidence"] == extract_module.GUESSED


async def test_handler_routes_text_items_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def record(text: str, work_type: str, http, log: dict) -> tuple[dict, dict]:
        return {"record": {"Название": text}}, {}

    monkeypatch.setattr(extraction_service, "extract_record", record)
    storage, publisher = FakeStorage(), FakePublisher()
    handler = extraction_service.ExtractionHandler(storage, publisher, None)
    dto = ItemCreatedDTO(item_id=uuid.uuid4(), work_type="publication", text="Запись")
    await handler(dto, Delivery(False, 0, 0))
    assert publisher.published[0][1].fields == {"record": {"Название": "Запись"}}


async def test_llm_circuit_opens_and_requeues() -> None:
    attempts = []

    def down(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("нет LLM")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        for _ in range(5):
            with pytest.raises(extract_module.LLMUnavailable):
                await extract_module.ask_llm(http, "текст", {})
        with pytest.raises(extract_module.LLMUnavailable, match="временно недоступен"):
            await extract_module.ask_llm(http, "текст", {})  # без похода к LLM: сообщение уйдёт в повтор
    assert len(attempts) == 5
