"""Подписи значений, склонения и форматирование: всё, что сервер отдаёт фронту уже готовым текстом."""

from datetime import date

WORK_TYPE = {"vkr": "ВКР", "coursework": "Курсовая", "publication": "Публикация", "talk": "Выступление"}
PUB_KIND = {
    "journal_article": "Статья в журнале",
    "proceedings_paper": "Статья в сборнике",
    "abstract": "Тезисы",
    "book_chapter": "Глава в книге",
    "monograph": "Монография",
    "textbook": "Учебное пособие",
    "other": "Другое",
}
COURSEWORK_KIND = {"work": "Курсовая работа", "project": "Курсовой проект"}
SUBJECT_SCHEME = {"grnti": "ГРНТИ", "oecd": "ОЕСР", "wos": "Web of Science", "asjc": "Scopus (ASJC)"}
MATERIAL_KIND = {"text": "Текст", "slides": "Презентация", "data": "Данные, код", "other": "Другое"}
INDEXING = {"scopus": "Scopus", "wos": "WoS", "vak": "ВАК", "rinc": "РИНЦ", "core_rinc": "Ядро РИНЦ"}
CONF_LEVEL = {
    "international": "Международная",
    "national": "Всероссийская",
    "regional": "Региональная",
    "university": "Вузовская",
}
TALK_KIND = {
    "plenary": "Пленарный",
    "section": "Секционный",
    "poster": "Стендовый",
    "invited": "Приглашённый",
    "other": "Другой",
}
TALK_FORMAT = {"offline": "Очно", "online": "Онлайн", "hybrid": "Смешанный"}
LEVEL = {"bachelor": "Бакалавриат", "specialist": "Специалитет", "master": "Магистратура"}
ACCESS = {"full": "Карточка и текст", "card_only": "Только карточка", "hidden": "Скрыта"}
STATUS = {"draft": "Черновик", "published": "Опубликована", "rejected": "Отклонена"}
PERSON_KIND = {"staff": "Преподаватель", "student": "Студент", "graduate": "Выпускник", "external": "Внешний автор"}
LANGUAGE = {"ru": "Русский", "en": "Английский", "other": "Другой"}

# Сокращения в выходных данных — на языке издания (ГОСТ Р 7.0.100–2018): у английской статьи Vol., No., P. и [et al.]
IMPRINT = {
    "ru": {
        "vol": "Т.",
        "no": "№",
        "pages": "С.",
        "book_pages": "с.",
        "et_al": "[и др.]",
        "monograph": "монография",
        "textbook": "учебное пособие",
    },
    "en": {
        "vol": "Vol.",
        "no": "No.",
        "pages": "P.",
        "book_pages": "p.",
        "et_al": "[et al.]",
        "monograph": "monograph",
        "textbook": "textbook",
    },
}


def imprint(work: dict) -> dict[str, str]:
    return IMPRINT["en" if work.get("language") == "en" else "ru"]


# Коды списков журналов и подписи; списки журнала берутся из справочника journals по ISSN.
JOURNAL_LISTS = {
    "white1": "Белый список, уровень 1",
    "white2": "Белый список, уровень 2",
    "white3": "Белый список, уровень 3",
    "white4": "Белый список, уровень 4",
    "vak_k1": "ВАК, К1",
    "vak_k2": "ВАК, К2",
    "vak_k3": "ВАК, К3",
    "rsci": "RSCI",
    "scopus_q1": "Scopus Q1",
    "scopus_q2": "Scopus Q2",
    "scopus_q3": "Scopus Q3",
    "scopus_q4": "Scopus Q4",
    "wos_q1": "WoS Q1",
    "wos_q2": "WoS Q2",
    "wos_q3": "WoS Q3",
    "wos_q4": "WoS Q4",
}

# Поля работы по пути формы: для замечаний и конфликтов DOI.
FIELD = {
    "title": "Название",
    "authors": "Авторы",
    "year": "Год",
    "department": "Кафедра",
    "annotation": "Аннотация",
    "keywords": "Ключевые слова",
    "language": "Язык",
    "supervisors": "Руководитель",
    "level": "Уровень",
    "direction": "Направление",
    "profile": "Профиль",
    "discipline": "Дисциплина",
    "pages": "Страницы",
    "kind": "Вид",
    "venue": "Журнал / сборник",
    "volume": "Том",
    "issue": "Номер",
    "publisher": "Издательство",
    "doi": "DOI",
    "issn": "ISSN",
    "isbn": "ISBN",
    "indexing": "Индексация",
    "event.name": "Конференция",
    "event.level": "Уровень конференции",
    "event.dateStart": "Дата начала",
    "presenters": "Докладчики",
    "subjects": "Классификаторы",
}

FORMS = {
    "vkr": ("ВКР", "ВКР", "ВКР"),
    "coursework": ("курсовая", "курсовые", "курсовых"),
    "publication": ("публикация", "публикации", "публикаций"),
    "talk": ("выступление", "выступления", "выступлений"),
}


def work_kind(work_type: str, kind: str | None) -> str:
    """Вид работы для карточки и «Моих работ»: «Статья в журнале», «Доклад (секционный)»."""
    if work_type == "publication":
        return PUB_KIND.get(kind or "", "Публикация")
    if work_type == "talk":
        return f"Доклад ({TALK_KIND.get(kind or 'other', '').lower()})"
    if work_type == "vkr":
        return "Выпускная квалификационная работа"
    return COURSEWORK_KIND[kind or "work"]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ('работа', 'работы', 'работ')) → «работ» — как Intl.PluralRules('ru') для целых."""
    one, few, many = forms
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _day_month(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def _full(d: date) -> str:
    return f"{_day_month(d)} {d.year} г."


def format_date_range(start: str, end: str | None = None) -> str:
    """«12–14 мая 2024 г.», «30 мая – 2 июня 2024 г.»"""
    a = date.fromisoformat(start[:10])
    if not end or end == start:
        return _full(a)
    b = date.fromisoformat(end[:10])
    if a.year != b.year:
        return f"{_full(a)} – {_full(b)}"
    if a.month == b.month:
        return f"{a.day}–{_full(b)}"
    return f"{_day_month(a)} – {_full(b)}"


def format_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, int(size / 1024 + 0.5))} КБ"
    return f"{size / 1024 / 1024:.1f} МБ".replace(".", ",").replace(",0 ", " ")  # «1,5 МБ», «500 МБ» — как во фронте


def from_label(labels: dict[str, str], text: str) -> str | None:
    """Обратный поиск для таблиц импорта: «Статья в журнале» → journal_article, «международный» → international."""
    t = text.strip().lower()
    if not t:
        return None
    for k, v in labels.items():
        if v.lower() == t or k == t:
            return k
    # ponytail: совпадение по 5 первым буквам ловит окончания рода и падежа; неоднозначные основы отвергаются
    stem = [k for k, v in labels.items() if v.lower()[:5] == t[:5]]
    return stem[0] if len(stem) == 1 else None
