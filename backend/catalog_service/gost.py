"""Библиографическая ссылка по ГОСТ Р 7.0.100–2018 (упрощённо, по хранимым полям). Перенос src/mocks/gost.ts."""

from typing import Any

from shared.labels import LEVEL, format_date_range
from shared.names import initials_first, parse_name

D = ". – "


def _heading(display: str) -> str:
    surname, initials = parse_name(display)
    return f"{surname}, {' '.join(i + '.' for i in initials)}" if initials else surname


def _authors_part(title: str, authors: list[dict], suffix: str = "") -> str:
    """1–3 автора — заголовок; 4 — перечисляются; 5 и больше — первые три [и др.]."""
    names = [initials_first(a["displayName"]) for a in authors]
    resp = f"{', '.join(names[:3])} [и др.]" if len(authors) >= 5 else ", ".join(names)
    head = f"{_heading(authors[0]['displayName'])} " if 0 < len(authors) <= 3 else ""
    return f"{head}{title}{suffix}{f' / {resp}' if resp else ''}"


def _end(s: str) -> str:
    return s if s.endswith((".", "!", "?")) else s + "."


def _tail(s: str, w: dict[str, Any]) -> str:
    if w["type"] == "publication" and w.get("doi"):
        return f"{s} – DOI {w['doi']}."
    if w.get("externalUrl"):
        return f"{s} – URL: {w['externalUrl']}."
    return s


def _publication(w: dict[str, Any]) -> str:
    if w["kind"] in ("monograph", "textbook"):
        label = "монография" if w["kind"] == "monograph" else "учебное пособие"
        s = _authors_part(w["title"], w["authors"], f" : {label}")
        s += D + ", ".join(str(x) for x in (w.get("publisher"), w.get("year")) if x)
        if w.get("pages"):
            s += D + f"{w['pages']} с."
        if w.get("isbn"):
            s += D + f"ISBN {w['isbn']}"
        return _tail(_end(s), w)
    s = _authors_part(w["title"], w["authors"]) + f" // {w.get('venue') or ''}"
    s += D + (
        f"{w['publisher']}, {w['year']}" if w.get("publisher") and w["kind"] != "journal_article" else str(w["year"])
    )
    vol = ", ".join(x for x in (w.get("volume") and f"Т. {w['volume']}", w.get("issue") and f"№ {w['issue']}") if x)
    if vol:
        s += D + vol
    if w.get("pages"):
        s += D + f"С. {w['pages']}"
    return _tail(_end(s), w)


def _student(w: dict[str, Any]) -> str:
    if w["type"] == "vkr":
        what = f"выпускная квалификационная работа ({LEVEL.get(w.get('level') or '', '').lower()})"
    else:
        what = "курсовой проект" if w.get("kind") == "project" else "курсовая работа"
        if w.get("discipline"):
            what += f" по дисциплине «{w['discipline']}»"
    d = w["direction"]
    s = _authors_part(w["title"], w["authors"], f" : {what} : {d.get('code') or ''} «{d.get('name') or ''}»")
    if w["supervisors"]:
        s += f" ; научный руководитель {', '.join(initials_first(a['displayName']) for a in w['supervisors'])}"
    if w.get("department"):
        s += f" ; {w['department']}"
    s += D + str(w["year"])
    if w.get("pages"):
        s += D + f"{w['pages']} с."
    return _tail(_end(s), w)


def _talk(w: dict[str, Any]) -> str:
    e = w["event"]
    dates = format_date_range(e["dateStart"], e.get("dateEnd")) if e.get("dateStart") else ""
    where = ", ".join(x for x in (e.get("city"), dates) if x)
    s = _authors_part(w["title"], w["authors"], " : доклад") + f" // {e.get('name') or ''} ({where})"
    return _tail(_end(s), w)


def gost_citation(w: dict[str, Any]) -> str:
    if w["type"] == "publication":
        return _publication(w)
    if w["type"] == "talk":
        return _talk(w)
    return _student(w)
