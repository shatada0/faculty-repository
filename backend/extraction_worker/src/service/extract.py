"""PDF → текст титульника и реферата → регулярки и LLM → поля карточки с confidence, посчитанным сервером."""

import asyncio
import json
import re
import sys
import time
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from extraction_worker.src.service import pdf_text
from extraction_worker.src.service.pdf_text import PASSWORD_EXIT
from shared.config import llm_config
from shared.logging import get_logger
from shared.people.names import parse_name

logger = get_logger(__name__)

PDF_TIMEOUT = 60
TEXT_LIMIT = 15000  # ~3–5 тыс. токенов: титульник, 2–3 страницы и реферат
REGEX = 0.95
IN_TEXT = 0.85
GUESSED = 0.4


class ItemError(Exception):
    """Ошибка на файле: item получает error с этим текстом, модератор его увидит."""


class LLMUnavailable(Exception):
    """LLM-сервер недоступен — временная ошибка, повтор через очередь."""


def cut(n: int) -> BeforeValidator:
    """Лимит в схеме — для грамматики сервера; не всякая грамматика его держит — обрезаем, а не теряем карточку."""
    return BeforeValidator(lambda v: v[:n] if isinstance(v, str | list) else v)


Name = Annotated[str, cut(200), Field(max_length=200)]


class ExtractedCard(BaseModel):
    """Поля карточки, которые не достаются регулярками. max_length — скрытый текст в PDF не раздует ответ."""

    title: Annotated[str | None, cut(500)] = Field(None, max_length=500)
    authors: Annotated[list[Name], cut(10)] = Field(default_factory=list, max_length=10)
    supervisors: Annotated[list[Name], cut(5)] = Field(default_factory=list, max_length=5)
    department: Annotated[str | None, cut(300)] = Field(None, max_length=300)
    annotation: Annotated[str | None, cut(3000)] = Field(None, max_length=3000)


# Все поля обязательные (значения — null или []): иначе грамматика Ollama даёт модели закрыть объект после
# первого поля, и GigaChat возвращал одно название. Разбор ответа при этом прощает пропуски — по умолчаниям модели.
CARD_SCHEMA = ExtractedCard.model_json_schema() | {"required": list(ExtractedCard.model_fields)}


EXTRACT_PROMPT = """Ты извлекаешь сведения из титульного листа и реферата студенческой работы (ВКР или курсовой).
Верни только JSON по схеме. Значения бери из текста дословно, ничего не придумывай; если значения нет — null или пустой список.
- title: тема работы без кавычек;
- authors: ФИО студентов-авторов, как в тексте;
- supervisors: ФИО научных руководителей без должностей и степеней;
- department: полное название кафедры («Кафедра …»);
- annotation: 1–3 предложения из реферата или аннотации о сути работы, дословно, без «Работа содержит …» и ключевых слов."""


def norm(s: str) -> str:
    return " ".join(s.lower().replace("ё", "е").split())


def dehyphen(page: str) -> str:
    """«интернет-\nмагазинов», «k-\nmeans» — перенос внутри слова, а не два слова."""
    return re.sub(r"(\w)-\n(?=\w)", r"\1-", page)


async def pdf_pages(data: bytes) -> list[str]:
    """Текст первых страниц в подпроцессе: без shell, со списком аргументов и таймаутом."""
    if not data.startswith(b"%PDF-"):
        raise ItemError("Файл не является PDF")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        pdf_text.__file__,  # файлом, а не -m: иначе подпроцесс импортирует пакет воркера с httpx и конфигом
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), PDF_TIMEOUT)
    except TimeoutError:
        raise ItemError("Файл не удалось разобрать") from None
    finally:  # и при отмене снаружи (таймаут элемента, остановка): иначе pypdf крутится сиротой
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode == PASSWORD_EXIT:
        raise ItemError("PDF защищён паролем")
    if proc.returncode != 0:
        logger.warning("pypdf упал", extra={"stderr": err.decode(errors="replace")[-1000:]})
        raise ItemError("Файл не удалось разобрать")
    return json.loads(out)["pages"]


def select_text(pages: list[str]) -> str:
    """Титульник и следующие две страницы плюс страница «Реферат», если она дальше."""
    chosen = pages[:3]
    abstract = next((p for p in pages[3:] if re.search(r"\b(реферат|аннотация|abstract)\b", p, re.IGNORECASE)), None)
    if abstract:
        chosen.append(abstract)
    return "\n\n".join(chosen)[:TEXT_LIMIT]


LEVELS = (("магистр", "master"), ("специалист", "specialist"), ("бакалавр", "bachelor"))
BY_CODE = {"03": "bachelor", "04": "master", "05": "specialist"}


PROFILE = re.compile(
    r"^\s*(?:направленность\s*\(профиль\)|профиль(?:\s+подготовки)?|(?:магистерская|образовательная)\s+программа"
    r"|программа|специализация)\s*[:—–-]\s*(\S.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def profile(title_page: str) -> str | None:
    """Профиль — только по подписи на титульнике. LLM тут подставляла направление: строка есть в тексте, 0.85."""
    if not (m := PROFILE.search(title_page)):
        return None
    value = m[1].strip()
    for line in title_page[m.end() :].splitlines()[1:]:  # перенос: продолжение начинается со строчной буквы
        if not line.strip() or not line.strip()[0].islower():
            break
        value += " " + line.strip()
    return value.rstrip(" .;") or None


def caps_keywords(text: str) -> list[str] | None:
    """Ключевые слова капсом без подписи: строка с запятыми без строчных букв и её продолжения без строчных."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.count(",") < 2 or re.search(r"[а-яёa-z]", line) or not re.search(r"[А-ЯЁA-Z]{3}", line):
            continue
        block = line.strip()
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or re.search(r"[а-яёa-z]", nxt) or block.endswith("."):
                break
            block += ("" if block.endswith("-") else " ") + nxt.strip()
        return [k.strip(" .").lower() for k in block.split(",") if k.strip(" .")][:15]
    return None


# Конец списка: пустая строка, точка в конце строки или новая фраза («\nОбъектом …») не после запятой.
KEYWORDS = re.compile(
    r"ключевые слова\s*[:\-–—]\s*(.+?)(?:\n\s*\n|\.\s*\n|(?<![,;-])[ \t]*\n(?=[ \t]*[А-ЯЁA-Z][а-яёa-z])|$)",
    re.IGNORECASE | re.DOTALL,
)
VOLUME = re.compile(r"\bсодержит\s+\d+\s*(?:с\.|стр)|^\s*ключевые слова", re.IGNORECASE)


def clean_annotation(text: str | None) -> str | None:
    """Без «Работа содержит 83 с., 12 рис. …» и ключевых слов: модель иногда берёт их в аннотацию первой фразой."""
    if not text:
        return None
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if not VOLUME.search(s)]
    return " ".join(kept) or None


def by_regex(title_page: str, text: str) -> dict[str, Any]:
    """Что берётся без LLM: код направления, год, страницы, ключевые слова, уровень, профиль.

    text начинается с title_page (select_text), переносы в обоих уже склеены (dehyphen).
    """
    out: dict[str, Any] = {}
    if m := re.search(r"\b\d{2}\.\d{2}\.\d{2}\b", title_page):
        out["directionCode"] = m[0]
    if years := re.findall(r"\b(19[89]\d|20\d{2})\b(?:\s*г\.?)?\s*$", title_page.strip(), re.MULTILINE):
        out["year"] = int(years[-1])  # «Москва 2026» внизу титульника
    if m := re.search(r"(?:содержит|объ[её]м[а-я]*)\D{0,40}?(\d{1,4})\s*(?:с\.|стр)", text, re.IGNORECASE):
        out["pages"] = int(m[1])
    if m := KEYWORDS.search(text):
        out["keywords"] = [k.strip(" .").lower() for k in re.split(r"[,;]", " ".join(m[1].split())) if k.strip(" .")][
            :15
        ]
    elif caps := caps_keywords(text[len(title_page) :]):  # капсом в реферате; ФИО капсом на титульнике — не они
        out["keywords"] = caps
    if found := profile(title_page):
        out["profile"] = found
    # Код направления задаёт уровень по классификатору: NN.03 бакалавриат, .04, .05. Слово «магистр» бывает
    # и в регалиях руководителя — по нему только без кода.
    lowered = title_page.lower()
    if level := BY_CODE.get(out.get("directionCode", "")[3:5]) or next((v for w, v in LEVELS if w in lowered), None):
        out["level"] = level
    return out


def work_kind(title_page: str) -> dict[str, str]:
    """ВКР или курсовая — по титульнику: загружать можно вперемешку, тип выбирать не нужно."""
    lowered = title_page.lower()
    # ВКР первой, курсовая — только «курсовая работа/проект»: в темах бывают «онлайн-курсов», «курсов валют»
    if re.search(r"выпускн\w*\s+квалификационн|дипломн\w*\s+(?:работ|проект)", lowered):
        return {"workType": "vkr"}
    if m := re.search(r"курсов\w*\s+(работ|проект)", lowered):
        return {"workType": "coursework", "courseworkKind": "project" if m[1] == "проект" else "work"}
    return {}  # не понятно — тип из импорта


async def ask_llm(http: httpx.AsyncClient, text: str, log: dict) -> ExtractedCard:
    """/v1/chat/completions одинаков у vLLM, llama.cpp и Ollama: сервер меняется без правки кода."""
    started = time.perf_counter()
    try:
        response = await http.post(
            f"{llm_config.url}/v1/chat/completions",
            json={
                "model": llm_config.model,
                "temperature": 0,
                "max_tokens": 1500,
                "messages": [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": text}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "card", "schema": CARD_SCHEMA},
                },
                # Рассуждения для извлечения только тратят токены: Qwen3.5 съедала на них все 1500, ответ был пустым.
                # chat_template_kwargs понимает vLLM, reasoning_effort — Ollama; каждый сервер чужой ключ пропускает.
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_effort": "none",
            },
            timeout=llm_config.timeout_seconds,
        )
    except (httpx.ReadTimeout, httpx.WriteTimeout) as e:  # сервер жив, но не успел: повтор упрётся в то же
        logger.warning("LLM: таймаут", extra=log | {"url": llm_config.url, "timeout": llm_config.timeout_seconds})
        raise ItemError("ИИ не ответил") from e
    except httpx.TransportError as e:  # не подключились или соединение оборвалось: сервер перезапускается
        logger.warning("LLM-сервер недоступен", extra=log | {"url": llm_config.url, "error": repr(e)})
        raise LLMUnavailable(repr(e)) from e
    ctx = log | {
        "url": llm_config.url,
        "model": llm_config.model,
        "status": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
    if response.status_code >= 500:
        logger.warning("LLM ответил ошибкой", extra=ctx | {"body": response.text[:500]})
        raise LLMUnavailable(f"HTTP {response.status_code}")
    if response.status_code != 200:
        logger.error("LLM отклонил запрос", extra=ctx | {"body": response.text[:500]})
        raise ItemError("ИИ не ответил")
    content, finish = "", None
    try:
        choice = response.json()["choices"][0]
        finish = choice.get("finish_reason")  # length — ответ обрезан по max_tokens
        content = choice["message"]["content"] or ""  # null — все токены ушли на рассуждения
        return ExtractedCard.model_validate_json(content)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, ValidationError) as e:
        log["stage"] = "parse"
        # Сам ответ в лог не пишем: в нём ФИО и аннотация.
        logger.warning(
            "Ответ модели не разобран",
            extra=ctx | {"stage": "parse", "finish_reason": finish, "chars": len(content), "error": type(e).__name__},
        )
        raise ItemError("ИИ вернул неверный ответ") from e


def _confidence(value: Any, text: str, *, name: bool = False, loose: bool = False) -> float:
    """Есть в исходном тексте (пробелы, регистр, «ё» нормализованы) — 0.85; нет — вероятно, выдумано.

    У ФИО сверяем фамилию. Аннотацию (loose) модель правит: «является» → «являются», склеивает перенос —
    поэтому считаем долю её слов, найденных в тексте, а не дословное совпадение начала.
    """
    if isinstance(value, list):
        return min((_confidence(v, text, name=name) for v in value), default=GUESSED)
    if loose:
        words = re.findall(r"\w+", norm(str(value)))
        have = set(re.findall(r"\w+", text))
        return IN_TEXT if words and sum(w in have for w in words) / len(words) >= 0.8 else GUESSED
    words = norm(str(value))[:60]
    if words and words in text:
        return IN_TEXT
    surname = norm(parse_name(str(value))[0]) if name else ""
    return IN_TEXT if len(surname) > 3 and surname in text else GUESSED


async def extract(pdf: bytes, http: httpx.AsyncClient, log: dict) -> tuple[dict[str, Any], dict[str, dict]]:
    """→ (fields, field_meta). Подтверждение по базе (0.95) добавит import_service: у воркера базы нет.

    log["stage"] — где сейчас разбор: по нему воркер пишет, на какой стадии упал файл.
    """
    started = time.perf_counter()
    log["stage"] = "pdf"
    pages = [dehyphen(p) for p in await pdf_pages(pdf)]
    text = select_text(pages)
    logger.info(
        "Текст извлечён",
        extra=log | {"pages": len(pages), "duration_ms": round((time.perf_counter() - started) * 1000)},
    )
    if not norm(text):
        raise ItemError("Скан без текстового слоя")  # OCR добавим, если сканов окажется много
    fields = by_regex(pages[0] if pages else "", text)
    meta = {k: {"source": "ai", "confidence": REGEX} for k in fields}
    started = time.perf_counter()
    log["stage"] = "ai"
    card = await ask_llm(http, text, log)
    logger.info("Карточка от модели", extra=log | {"duration_ms": round((time.perf_counter() - started) * 1000)})
    normalized = norm(text)
    card.annotation = clean_annotation(card.annotation)
    for key, value in card.model_dump().items():
        if value in (None, "", []):
            continue
        fields[key] = value
        meta[key] = {
            "source": "ai",
            "confidence": _confidence(
                value, normalized, name=key in ("authors", "supervisors"), loose=key == "annotation"
            ),
        }
    fields["language"] = "ru" if re.search(r"[а-яё]", fields.get("title") or text, re.IGNORECASE) else "en"
    fields |= work_kind(pages[0] if pages else "")  # не поле карточки: в meta не попадает
    return fields, meta
