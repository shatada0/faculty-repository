"""ФИО: разбор любых написаний, короткая форма и ключи сопоставления авторов (перенос src/mocks/names.ts)."""

import re

CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
_INITIAL = re.compile(r"^[^\W\d_]\.?$")
_LATIN_START = re.compile(r"^[A-Za-z]")


def translit(s: str) -> str:
    out = []
    for ch in s:
        lower = ch.lower()
        t = CYR.get(lower)
        if t is None:
            out.append(ch)
        else:
            out.append(t if ch == lower else t[:1].upper() + t[1:])
    return "".join(out)


def parse_name(display: str) -> tuple[str, list[str]]:
    """«Иванов Иван Иванович» | «Иванов И. И.» | «И.И. Иванов» | «I. Ivanov» → фамилия и первые буквы."""
    tokens = re.sub(r"\.(?=\S)", ". ", display).split()
    if not tokens:
        return "", []
    surname_idx = 0
    if any(_INITIAL.match(t) for t in tokens):
        surname_idx = next((i for i, t in enumerate(tokens) if not _INITIAL.match(t)), 0)
    elif len(tokens) == 2 and _LATIN_START.match(tokens[0]):
        surname_idx = 1  # «John Smith»: западный порядок
    surname = tokens[surname_idx].removesuffix(",")
    initials = [t[0].upper() for i, t in enumerate(tokens) if i != surname_idx]
    return surname, initials


def short_name(full_name: str) -> str:
    """«Иванов Иван Иванович» → «Иванов И. И.»"""
    surname, initials = parse_name(full_name)
    return " ".join([surname, *(i + "." for i in initials)])


def initials_first(display: str) -> str:
    """«Иванов И. И.» → «И. И. Иванов»"""
    surname, initials = parse_name(display)
    return " ".join([*(i + "." for i in initials), surname])


def exact_key(name: str) -> str:
    """Точное совпадение «Иванов И.И.» и «Иванов И. И.»: без пробелов, точек и запятых, ё → е."""
    return re.sub(r"[\s.,]", "", name.lower()).replace("ё", "е")


def short_key(display: str) -> str:
    """Латинская фамилия и первая буква имени: «Иванов И.И.» и «I. Ivanov» → «ivanov:i»."""
    surname, initials = parse_name(display)
    s = translit(surname).lower().replace("kh", "h")
    i = translit(initials[0]).lower()[:1] if initials else ""
    return f"{s}:{i}"


def latin_name(full_name: str) -> str:
    """«Орлов Дмитрий Сергеевич» → «D. S. Orlov»."""
    surname, initials = parse_name(full_name)
    return " ".join([*(translit(i) + "." for i in initials), translit(surname)])


def name_query(q: str) -> str | None:
    """Префиксный tsquery: каждое слово запроса — начало слова имени. Только буквы и цифры: синтаксис не сломать."""
    tokens = re.findall(r"[^\W_]+", q.lower().replace("ё", "е"))
    return " & ".join(f"{t}:*" for t in tokens) or None
