"""BibTeX и RIS → строки шаблона публикаций, дальше — как импорт таблицы.

Эти форматы выгружают Scopus, Web of Science, Google Scholar, Zotero и Mendeley. Записи из Scopus и WoS
получают отметку индексации. Парсер свой: два формата по десятку полей, библиотека ради них не нужна.
"""

import re

from shared.works.labels import INDEXING

EXTENSIONS = (".bib", ".ris")
BIB_KIND = {
    "article": "journal_article",
    "inproceedings": "proceedings_paper",
    "conference": "proceedings_paper",
    "proceedings": "proceedings_paper",
    "incollection": "book_chapter",
    "inbook": "book_chapter",
    "book": "monograph",
}
RIS_KIND = {
    "JOUR": "journal_article",
    "EJOUR": "journal_article",
    "MGZN": "journal_article",
    "CONF": "proceedings_paper",
    "CPAPER": "proceedings_paper",
    "CHAP": "book_chapter",
    "BOOK": "monograph",
    "EBOOK": "monograph",
    "ABST": "abstract",
}


def is_bibliography(filename: str) -> bool:
    return filename.lower().endswith(EXTENSIONS)


def _name(raw: str) -> str:
    """«Oseledets, Ivan V.», «Ivan V. Oseledets», «Иванов, И. П.» → «Oseledets I. V.», «Иванов И. П.»."""
    raw = " ".join(raw.split())
    if "," in raw:
        family, given = (x.strip() for x in raw.split(",", 1))
    else:
        *parts, family = raw.split(" ") if raw else [""]
        given = " ".join(parts)
    initials = " ".join(f"{p[0]}." for p in re.split(r"[\s.\-]+", given) if p)
    return " ".join(x for x in (family, initials) if x)


def _row(kind: str, f: dict[str, str], authors: list[str], indexing: list[str]) -> dict[str, str]:
    first, last = f.get("start", ""), f.get("end", "")
    pages = f.get("pages") or (f"{first}–{last}" if first and last else first)
    return {
        "Авторы": "; ".join(_name(a) for a in authors if a.strip()),
        "Название": f.get("title", ""),
        "Вид": kind,
        "Журнал/сборник": f.get("venue", ""),
        "Год": (re.search(r"\d{4}", f.get("year", "")) or [""])[0],
        "Том": f.get("volume", ""),
        "Номер": f.get("issue", ""),
        "Страницы": pages.replace("--", "–").replace("-", "–"),
        "Издательство": f.get("publisher", ""),
        "DOI": re.sub(r"^https?://(dx\.)?doi\.org/", "", f.get("doi", ""), flags=re.IGNORECASE),
        "ISSN/ISBN": f.get("issn", ""),
        "Индексация": ", ".join(INDEXING[i] for i in dict.fromkeys(indexing)),
        "Ссылка": f.get("url", ""),
        "Кафедра": "",
    }


# ---------- BibTeX ----------


def _tex(s: str) -> str:
    """Убираем разметку LaTeX: {\\"o} → o, \\& → &, {Title Case} → Title Case."""
    s = re.sub(r"\\([&%$#_])", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\s?|\\[^a-zA-Z\s]", "", s)
    return " ".join(s.replace("{", "").replace("}", "").split())


def _bib_value(body: str, i: int) -> tuple[str, int]:
    """Значение поля с позиции i: {...} со вложенными скобками, "..." или голое слово до запятой."""
    if body[i] == "{":
        depth, j = 0, i
        while j < len(body):
            depth += {"{": 1, "}": -1}.get(body[j], 0)
            j += 1
            if depth == 0:
                break
        return body[i + 1 : j - 1], j
    if body[i] == '"':
        j = i + 1
        while j < len(body) and (body[j] != '"' or body[j - 1] == "\\"):
            j += 1
        return body[i + 1 : j], j + 1
    m = re.match(r"[^,}\s]*", body[i:])
    return m[0], i + len(m[0])


def _bib_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    i = body.find(",") + 1  # после ключа записи
    while m := re.compile(r"\s*([\w\-]+)\s*=\s*").match(body, i):
        value, i = _bib_value(body, m.end())
        fields[m[1].lower()] = value
        i = body.find(",", i) + 1 or len(body)
    return fields


def parse_bibtex(text: str) -> list[dict[str, str]]:
    out, pos = [], 0
    while m := re.compile(r"@(\w+)\s*[{(]").search(text, pos):
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "(": 1, "}": -1, ")": -1}.get(text[j], 0)
            j += 1
        pos = j
        kind = m[1].lower()
        if kind in ("comment", "preamble", "string"):
            continue
        raw = _bib_fields(text[m.end() : j - 1])
        f = {k: _tex(v) for k, v in raw.items()}
        indexing = []
        if "scopus" in (f.get("source", "") + f.get("url", "")).lower():
            indexing.append("scopus")
        if f.get("unique-id", "").upper().startswith("WOS:") or "web-of-science-index" in f:
            indexing.append("wos")
        fields = {
            "title": f.get("title", ""),
            "venue": f.get("journal") or f.get("booktitle") or f.get("series", ""),
            "year": f.get("year", ""),
            "volume": f.get("volume", ""),
            "issue": f.get("number", ""),
            "pages": f.get("pages", "") or f.get("article-number", ""),
            "publisher": f.get("publisher", ""),
            "doi": f.get("doi", ""),
            "issn": f.get("issn", "") or f.get("isbn", ""),
            "url": f.get("url", ""),
        }
        authors = re.split(r"\s+and\s+", f.get("author", ""))
        out.append(_row(BIB_KIND.get(kind, "other"), fields, authors, indexing))
    return out


# ---------- RIS ----------

RIS_LINE = re.compile(r"^([A-Z][A-Z0-9])  -\s?(.*)$")


def parse_ris(text: str) -> list[dict[str, str]]:
    out, tags = [], {}
    for line in text.splitlines():
        m = RIS_LINE.match(line.strip("\ufeff"))
        if not m:
            continue
        tag, value = m[1], m[2].strip()
        if tag == "TY":
            tags = {"TY": [value]}
        elif tag == "ER":
            out.append(_ris_row(tags))
            tags = {}
        elif tags:
            tags.setdefault(tag, []).append(value)
    return out


def _ris_row(t: dict[str, list[str]]) -> dict[str, str]:
    def one(*names: str) -> str:
        return next((t[n][0] for n in names if t.get(n) and t[n][0]), "")

    marks = " ".join(v for n in ("DB", "DP", "UR", "AN", "UT", "M3") for v in t.get(n, [])).lower()
    indexing = [code for code, hit in (("scopus", "scopus"), ("wos", "web of science")) if hit in marks]
    if "wos:" in marks and "wos" not in indexing:
        indexing.append("wos")
    fields = {
        "title": one("TI", "T1", "CT"),
        "venue": one("T2", "JF", "JO", "JA", "BT", "T3"),
        "year": one("PY", "Y1", "DA"),
        "volume": one("VL"),
        "issue": one("IS"),
        "start": one("SP"),
        "end": one("EP"),
        "publisher": one("PB"),
        "doi": one("DO"),
        "issn": one("SN"),
        "url": one("UR"),
    }
    return _row(RIS_KIND.get(one("TY"), "other"), fields, t.get("AU", []) + t.get("A1", []), indexing)


def read_bibliography(name: str, data: bytes) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return parse_ris(text) if name.lower().endswith(".ris") else parse_bibtex(text)
    raise ValueError("Кодировка не распознана")
