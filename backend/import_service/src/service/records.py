"""Строка таблицы или ответ ИИ → поля черновика в форме контракта и их источники (fieldMeta)."""

import re
from typing import Any

from shared.works import labels as L

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
CONSENT = re.compile(r"^(да|yes|1|true|\+)$", re.IGNORECASE)
# ISSN узнаём по форме NNNN-NNNX; всё остальное в колонке — ISBN, в том числе ISBN-10 «5-02-013850-9»
ISSN = re.compile(r"(issn\s*)?\d{4}-?\d{3}[\dx]", re.IGNORECASE)
# Поле черновика → колонка шаблона: человеку понятнее, в какой ячейке ошибка.
COLUMN_OF = {
    "title": "Название",
    "year": "Год",
    "externalUrl": "Ссылка",
    "event.dateStart": "Дата начала",
    "event.dateEnd": "Дата окончания",
    "event.url": "Ссылка",
    "pages": "Страницы",
    "department": "Кафедра",
}


def column(work_type: str, field: str) -> str:
    """Колонка, где ошибка. Год доклада берётся из даты начала: колонки «Год» в шаблоне докладов нет."""
    if work_type == "talk" and field == "year":
        return "Дата начала"
    return COLUMN_OF.get(field, L.FIELD.get(field, field))


def split_names(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n]+", s or "") if x.strip()]


def parse_date(s: str) -> str:
    """«12.05.2024» → «2024-05-12»; ISO и прочее — как есть (проверит схема записи)."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s.strip())
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else s.strip()


def language(title: str) -> str:
    return "ru" if CYRILLIC.search(title or "") else "en"


def _int(s: str) -> int | str:
    return int(s) if s.strip().isdigit() else s  # не число — пусть схема записи скажет, где ошибка


def _filled(record: dict[str, str], mapping: dict[str, str]) -> dict[str, dict]:
    return {field: {"source": "table"} for field, column in mapping.items() if record.get(column)}


def publication(r: dict[str, str]) -> tuple[dict[str, Any], dict[str, dict], list[str]]:
    """→ (поля, fieldMeta, имена авторов для сопоставления)."""
    idn = r.get("ISSN/ISBN", "").strip()
    is_issn = bool(ISSN.fullmatch(idn))
    indexing = [code for x in re.split(r"[,;]", r.get("Индексация", "")) if (code := L.from_label(L.INDEXING, x))]
    fields = {
        "title": r["Название"],
        "year": _int(r["Год"]),
        "department": r.get("Кафедра"),
        "keywords": [],
        "language": language(r["Название"]),
        "accessLevel": "card_only",
        "kind": L.from_label(L.PUB_KIND, r["Вид"]) or "other",
        "venue": r["Журнал/сборник"],
        "volume": r.get("Том"),
        "issue": r.get("Номер"),
        "pages": r.get("Страницы"),
        "publisher": r.get("Издательство"),
        "doi": r.get("DOI"),
        "isbn": None if is_issn else re.sub(r"^isbn\s*", "", idn, flags=re.IGNORECASE),
        "issn": idn if is_issn else None,
        "indexing": list(dict.fromkeys(indexing)),
        "externalUrl": r.get("Ссылка"),
    }
    meta = _filled(
        r,
        {
            "title": "Название",
            "authors": "Авторы",
            "year": "Год",
            "department": "Кафедра",
            "kind": "Вид",
            "venue": "Журнал/сборник",
            "volume": "Том",
            "issue": "Номер",
            "pages": "Страницы",
            "publisher": "Издательство",
            "doi": "DOI",
            "issn" if is_issn else "isbn": "ISSN/ISBN",
            "indexing": "Индексация",
        },
    )
    return fields, meta, split_names(r["Авторы"])


def talk(r: dict[str, str]) -> tuple[dict[str, Any], dict[str, dict], list[str], list[str]]:
    """→ (поля, fieldMeta, докладчики, соавторы)."""
    start = parse_date(r["Дата начала"])
    fields = {
        "title": r["Название доклада"],
        "year": _int(start[:4]),
        "department": r.get("Кафедра"),
        "keywords": [],
        "language": language(r["Название доклада"]),
        "accessLevel": "full",
        "event": {
            "name": r["Конференция"],
            "level": L.from_label(L.CONF_LEVEL, r["Уровень"]) or "national",
            "city": r.get("Город"),
            "country": r.get("Страна"),
            "dateStart": start,
            "dateEnd": parse_date(r["Дата окончания"]) if r.get("Дата окончания") else None,
            "url": r.get("Ссылка"),
        },
        "kind": L.from_label(L.TALK_KIND, r.get("Вид доклада", "")) or "section",
        "format": L.from_label(L.TALK_FORMAT, r.get("Формат", "")) or "offline",
        "award": r.get("Награда"),
    }
    meta = _filled(
        r,
        {
            "title": "Название доклада",
            "authors": "Докладчики",
            "presenters": "Докладчики",
            "year": "Дата начала",
            "department": "Кафедра",
            "event.name": "Конференция",
            "event.level": "Уровень",
            "event.dateStart": "Дата начала",
            "kind": "Вид доклада",
            "format": "Формат",
        },
    )
    return fields, meta, split_names(r["Докладчики"]), split_names(r.get("Соавторы", ""))


def student(r: dict[str, str], work_type: str) -> tuple[dict[str, Any], dict[str, dict]]:
    """Строка таблицы сведений к архиву ВКР или курсовых: только заполненные ячейки, пустые оставляют найденное ИИ.

    ФИО автора и руководителя сопоставляет сервис.
    """
    consent = bool(CONSENT.match(r.get("Согласие на публикацию текста", "").strip()))
    kind = r.get("Работа или проект", "")
    fields = {
        "title": r.get("Тема"),
        "year": _int(r.get("Год", "")),
        "department": r.get("Кафедра"),
        "level": L.from_label(L.LEVEL, r.get("Уровень", "")),
        "direction": {"code": code, "name": r.get("Название направления")}
        if (code := r.get("Код направления"))
        else None,
        "profile": r.get("Профиль"),
        "discipline": r.get("Дисциплина"),
        "consent": consent or None,  # «нет» и пусто — как по умолчанию
        "accessLevel": "full" if consent and work_type == "vkr" else None,
        "kind": ("project" if re.search("проект", kind, re.IGNORECASE) else "work")
        if kind and work_type == "coursework"
        else None,
    }
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    meta = _filled(
        r,
        {
            "title": "Тема",
            "authors": "ФИО автора",
            "supervisors": "ФИО руководителя",
            "year": "Год",
            "level": "Уровень",
            "direction": "Код направления",
            "department": "Кафедра",
            "profile": "Профиль",
            "discipline": "Дисциплина",
            "kind": "Работа или проект",
        },
    )
    # «Уровень: аспирантура» не распознан — поле осталось от ИИ, и источник у него не таблица
    return fields, {k: m for k, m in meta.items() if k in fields or k in ("authors", "supervisors")}


def row_error(missing: list[str]) -> str:
    if "Год" in missing:
        return "Не указан год"
    if "Дата начала" in missing:
        return "Не указана дата начала"
    return f"Не заполнено: {', '.join(missing)}"
