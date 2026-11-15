"""PDF → текст титульника и реферата (у скана — через OCR) → регулярки и LLM → поля карточки с confidence, посчитанным сервером."""

import asyncio
import difflib
import json
import re
import shutil
import sys
import time
from typing import Annotated, Any, Literal

import httpx
from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from extraction_worker.src.service import ocr, pdf_text
from extraction_worker.src.service.pdf_text import PASSWORD_EXIT
from shared.config import llm_config
from shared.logging import get_logger
from shared.people.names import parse_name, translit
from shared.resilience import CircuitOpenError, guard
from shared.works.labels import CONF_LEVEL, PUB_KIND

logger = get_logger(__name__)

PDF_TIMEOUT = 60
OCR_TIMEOUT = ocr.OCR_PAGES * ocr.PAGE_TIMEOUT + 30
OCR = shutil.which("tesseract") is not None  # в образе воркера есть; без него скан — ошибка, как раньше
# Ниже порога lowConfidence (0.7): OCR путает буквы («Сс ПОМОЩЬЮ»), карточку со скана смотрит модератор,
# без автопубликации. Руководителя, найденного в базе, import_service всё равно поднимет до 0.95.
OCR_CONFIDENCE = 0.6
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

Short = Annotated[str | None, cut(300)]


class PublicationRecord(BaseModel):
    """Запись списка публикаций → колонки шаблона «Публикации» (COLUMNS ниже)."""

    authors: Annotated[list[Name], cut(30)] = Field(default_factory=list, max_length=30)
    title: Annotated[str | None, cut(500)] = Field(None, max_length=500)
    kind: Literal[*PUB_KIND.values()] | None = None  # type: ignore[valid-type]
    venue: Short = None
    year: Short = None
    volume: Short = None
    issue: Short = None
    pages: Short = None
    publisher: Short = None
    doi: Short = None
    issn_isbn: Short = None


class TalkRecord(BaseModel):
    """Запись программы конференции или списка докладов → колонки шаблона «Доклады»."""

    presenters: Annotated[list[Name], cut(10)] = Field(default_factory=list, max_length=10)
    coauthors: Annotated[list[Name], cut(20)] = Field(default_factory=list, max_length=20)
    title: Annotated[str | None, cut(500)] = Field(None, max_length=500)
    event: Short = None
    level: Literal[*CONF_LEVEL.values()] | None = None  # type: ignore[valid-type]
    city: Short = None
    country: Short = None
    date_start: Short = None
    date_end: Short = None


COLUMNS = {
    "publication": {
        "authors": "Авторы",
        "title": "Название",
        "kind": "Вид",
        "venue": "Журнал/сборник",
        "year": "Год",
        "volume": "Том",
        "issue": "Номер",
        "pages": "Страницы",
        "publisher": "Издательство",
        "doi": "DOI",
        "issn_isbn": "ISSN/ISBN",
    },
    "talk": {
        "presenters": "Докладчики",
        "coauthors": "Соавторы",
        "title": "Название доклада",
        "event": "Конференция",
        "level": "Уровень",
        "city": "Город",
        "country": "Страна",
        "date_start": "Дата начала",
        "date_end": "Дата окончания",
    },
}
NAME_FIELDS = {"authors", "presenters", "coauthors"}
RECORD_PROMPT = {
    "publication": """Ты разбираешь одну библиографическую запись публикации (ГОСТ, APA, список из eLIBRARY или отчёта).
Верни только JSON по схеме. Значения бери из записи дословно, ничего не придумывай; чего нет — null или пустой список.
- authors: каждый автор отдельно, как в записи («Иванов И. И.», «I. I. Ivanov»);
- title: название работы без кавычек и без авторов;
- kind: вид публикации — статья в журнале, статья в сборнике (труды конференции), тезисы, глава в книге, монография, учебное пособие;
- venue: журнал или сборник (для монографии — null); year: год; volume: том; issue: номер выпуска;
- pages: страницы («12–18») или число страниц книги; publisher: издательство; doi: DOI без https://doi.org/;
- issn_isbn: ISSN или ISBN, если есть.""",
    "talk": """Ты разбираешь одну запись программы конференции или списка докладов.
Верни только JSON по схеме. Значения бери из записи дословно, ничего не придумывай; чего нет — null или пустой список.
- presenters: докладчики; coauthors: остальные авторы доклада;
- title: тема доклада без кавычек; event: название конференции; level: уровень конференции, если назван;
- city, country: место проведения; date_start, date_end: даты в формате ДД.ММ.ГГГГ, только если они есть в записи.""",
}


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


async def _pages(script: str, data: bytes, seconds: int) -> tuple[int | None, bytes, bytes]:
    """Скрипт-подпроцесс над PDF: без shell, со списком аргументов и таймаутом. → (код, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        script,  # файлом, а не -m: иначе подпроцесс импортирует пакет воркера с httpx и конфигом
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), seconds)
    except TimeoutError:
        raise ItemError("Файл не удалось разобрать") from None
    finally:  # и при отмене снаружи (таймаут элемента, остановка): иначе разбор крутится сиротой
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    return proc.returncode, out, err


async def pdf_pages(data: bytes) -> list[str]:
    """Текстовый слой первых страниц (pypdf)."""
    if not data.startswith(b"%PDF-"):
        raise ItemError("Файл не является PDF")
    code, out, err = await _pages(pdf_text.__file__, data, PDF_TIMEOUT)
    if code == PASSWORD_EXIT:
        raise ItemError("PDF защищён паролем")
    if code != 0:
        logger.warning("pypdf упал", extra={"stderr": err.decode(errors="replace")[-1000:]})
        raise ItemError("Файл не удалось разобрать")
    return json.loads(out)["pages"]


async def ocr_pages(data: bytes) -> list[str]:
    """Текст первых страниц скана (PDFium + Tesseract)."""
    code, out, err = await _pages(ocr.__file__, data, OCR_TIMEOUT)
    if code != 0:
        logger.warning("OCR упал", extra={"stderr": err.decode(errors="replace")[-1000:]})
        raise ItemError("Скан не удалось распознать")
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
    # (?-i:…): заглавная — именно заглавная; под IGNORECASE список рвался на любом переносе («градиентный\nбустинг»)
    r"ключевые слова\s*[:\-–—]\s*(.+?)(?:\n\s*\n|\.\s*\n|(?<![,;-])[ \t]*\n(?=(?-i:[ \t]*[А-ЯЁA-Z][а-яёa-z]))|$)",
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
    # «Москва 2026», «2025г.», «2026 год», «2026 г .» — последней строкой титульника
    if years := re.findall(r"(?<!\d)(19[89]\d|20\d{2})\s*(?:г(?:од)?\s*\.?)?\s*$", title_page.strip(), re.MULTILINE):
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
    return await _chat(http, EXTRACT_PROMPT, text, ExtractedCard, log)


async def _chat[M: BaseModel](http: httpx.AsyncClient, prompt: str, text: str, model: type[M], log: dict) -> M:
    """/v1/chat/completions одинаков у vLLM, llama.cpp и Ollama: сервер меняется без правки кода.
    Схема ответа — из модели, все поля обязательны: так Qwen не пропускает их."""
    started = time.perf_counter()
    schema = model.model_json_schema() | {"required": list(model.model_fields)}
    try:
        response = await guard("LLM").request(
            http,
            "POST",
            f"{llm_config.url}/v1/chat/completions",
            json={
                "model": llm_config.model,
                "temperature": 0,
                "max_tokens": 1500,
                "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "card", "schema": schema},
                },
                # Рассуждения для извлечения только тратят токены: Qwen3.5 съедала на них все 1500, ответ был пустым.
                # chat_template_kwargs понимает vLLM, reasoning_effort — Ollama; каждый сервер чужой ключ пропускает.
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_effort": "none",
            },
            timeout=llm_config.timeout_seconds,
        )
    except CircuitOpenError as e:  # LLM только что падал подряд: сообщение вернётся через очередь повторов
        logger.warning("LLM: цепь разомкнута", extra=log | {"url": llm_config.url})
        raise LLMUnavailable(str(e)) from e
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
        answer = model.model_validate_json(content)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, ValidationError) as e:
        log["stage"] = "parse"
        # Сам ответ в лог не пишем: в нём ФИО и аннотация.
        logger.warning(
            "Ответ модели не разобран",
            extra=ctx | {"stage": "parse", "finish_reason": finish, "chars": len(content), "error": type(e).__name__},
        )
        raise ItemError("ИИ вернул неверный ответ") from e
    # время каждого ответа — панели «ИИ» в Grafana, с любым сервером: vLLM, Ollama, llama.cpp
    logger.info("LLM ответил", extra=ctx | {"finish_reason": finish})
    return answer


PATRONYMIC = re.compile(r"(вич|вна|ична|оглы|кызы)$", re.IGNORECASE)


def _name_key(s: str) -> str:
    return re.sub(r"[^a-z]", "", translit(s.replace("ё", "е").replace("Ё", "Е")).lower())


def fix_caps(name: str) -> str:
    """«ГАЙДАЙ-ТУРЛОВ Иван Павлович» → «Гайдай-Турлов Иван Павлович»: фамилия капсом с титульника."""
    # только слова из букв: инициалы «Ю.В.» не трогаем, иначе выйдет «Ю.в.»
    return " ".join(
        "-".join(p.capitalize() for p in t.split("-")) if re.fullmatch(r"[A-ZА-ЯЁ]{2,}(?:-[A-ZА-ЯЁ]{2,})*", t) else t
        for t in name.split()
    )


def snap_name(value: str, title_page: str) -> str:
    """ФИО от модели → то же ФИО, как оно написано на титульнике.

    Маленькая модель путает буквы фамилии капсом («ГОВОРАВА» вместо «ГОВОРОВА») и переводит имя в латиницу
    («Veiber Evgenia» вместо «ВЕЙБЕР Евгения Николаевна»). Похожего в тексте нет — значение остаётся, а низкий
    confidence покажет его модератору.
    """
    # с инициалами не притягиваем: «R.Sh. Azimov» на титульнике — «Р. Ш. Азимов», окно по словам их не сопоставит
    if "." in value or norm(value) in norm(title_page):
        return value
    key, n = _name_key(value), len(value.split())
    best, best_ratio = None, 0.8  # порог: опечатка в одну-две буквы проходит, другой человек — нет
    for line in title_page.splitlines():
        tokens = line.split()
        # окно ровно по числу слов: длиннее подхватывает обломки слов («ХР АМОВ» — так pypdf читает «ХРАМОВ»)
        for i in range(len(tokens) - n + 1):
            window = tokens[i : i + n]
            if not all(t[:1].isupper() for t in window):
                continue
            found = _name_key(" ".join(window))
            ratio = difflib.SequenceMatcher(None, key, found).ratio()
            # из текста — не короче значения модели: «Амов» из «ХР АМОВ» не заменит верное «Храмов»
            if ratio > best_ratio and len(found) >= len(key) - 1:
                rest = tokens[i + n : i + n + 1]  # «Veiber Evgenia» → с отчеством из текста
                best, best_ratio = window + (rest if rest and PATRONYMIC.search(rest[0]) else []), ratio
    return " ".join(best).strip(" ,;") if best else value


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
    scanned = not norm(text)
    if scanned:
        if not OCR:
            raise ItemError("Скан без текстового слоя")
        log["stage"] = "ocr"
        started = time.perf_counter()
        pages = [dehyphen(p) for p in await ocr_pages(pdf)]
        text = select_text(pages)
        logger.info(
            "Скан распознан",
            extra=log
            | {"pages": len(pages), "chars": len(text), "duration_ms": round((time.perf_counter() - started) * 1000)},
        )
        if not norm(text):
            raise ItemError("Скан не удалось распознать")
    fields = by_regex(pages[0] if pages else "", text)
    meta = {k: {"source": "ai", "confidence": REGEX} for k in fields}
    log["stage"] = "ai"
    card = await ask_llm(http, text, log)
    normalized = norm(text)
    card.annotation = clean_annotation(card.annotation)
    title_page = pages[0] if pages else ""
    for key, value in card.model_dump().items():
        if value in (None, "", []):
            continue
        if key in ("authors", "supervisors"):
            value = [fix_caps(snap_name(v, title_page)) for v in value]
        fields[key] = value
        meta[key] = {
            "source": "ai",
            "confidence": _confidence(
                value, normalized, name=key in ("authors", "supervisors"), loose=key == "annotation"
            ),
        }
    fields["language"] = "ru" if re.search(r"[а-яё]", fields.get("title") or text, re.IGNORECASE) else "en"
    fields |= work_kind(pages[0] if pages else "")  # не поле карточки: в meta не попадает
    if scanned:
        meta = {k: m | {"confidence": min(m["confidence"], OCR_CONFIDENCE)} for k, m in meta.items()}
    return fields, meta


async def extract_record(text: str, work_type: str, http: httpx.AsyncClient, log: dict) -> tuple[dict, dict]:
    """Запись списка текстом → ({"record": строка шаблона}, fieldMeta по колонкам).

    Уверенность — как у карточки ВКР: значение есть в записи дословно — 0.85, нет — 0.4. Значение из подписей
    (вид публикации, уровень) в тексте не встречается дословно — оно всегда 0.4, модератор его проверит.
    """
    log["stage"] = "ai"
    model = PublicationRecord if work_type == "publication" else TalkRecord
    answer = await _chat(http, RECORD_PROMPT[work_type], text, model, log)
    normalized = norm(text)
    record, meta = {}, {}
    for key, column in COLUMNS[work_type].items():
        value = getattr(answer, key)
        if value in (None, "", []):
            continue
        record[column] = "; ".join(value) if isinstance(value, list) else str(value).strip()
        confidence = _confidence(value, normalized, name=key in NAME_FIELDS)
        meta[column] = {"source": "ai", "confidence": confidence}
    return {"record": record}, meta
