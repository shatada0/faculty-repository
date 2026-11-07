"""Готовая карточка работы (WorkView): фронт только рисует её. Перенос src/mocks/view.ts."""

from typing import Any

from shared.works import labels as L


def _doi_url(doi: str) -> str:
    return f"https://doi.org/{doi}"


def _links(refs: list[dict]) -> list[dict]:
    """У преподавателей, студентов и выпускников есть страница; у внешних авторов — нет."""
    out = []
    for a in refs:
        link = {"name": a["displayName"]}
        if a.get("personId") and a["kind"] != "external":
            link["profileId"] = a["personId"]
        out.append(link)
    return out


def _row(label: str, text: Any = None, **extra: Any) -> list[dict]:
    """Строка «Сведений» или ничего, если показывать нечего."""
    extra = {k: v for k, v in extra.items() if v}
    if text or extra.get("people"):
        return [{"label": label, "text": str(text) if text else "", **extra}]
    return []


def _join(parts: list[Any], sep: str) -> str:
    return sep.join(str(p) for p in parts if p)


def work_view(w: dict[str, Any], materials: list[dict]) -> dict[str, Any]:
    """w — работа в форме контракта, materials — принятые материалы."""
    meta: list[dict] = []
    kind = L.work_kind(w["type"], w.get("kind"))
    if w["type"] == "publication":
        book = w["kind"] in ("monograph", "textbook")
        t = L.imprint(w)
        pages = w.get("pages") and (f"{w['pages']} {t['book_pages']}" if book else f"{t['pages']} {w['pages']}")
        volume = w.get("volume") and f"{t['vol']} {w['volume']}"
        imprint = _join([volume, w.get("issue") and f"{t['no']} {w['issue']}", pages], ", ")
        indexing = ", ".join(L.INDEXING[i] for i in w.get("indexing") or [])
        byline = [{"people": _links(w["authors"])}]
        where = _join([w.get("venue") or w.get("publisher"), w.get("year"), imprint], ". ")
        lists = ", ".join(w.get("journalLists") or [])
        if indexing:
            meta.append({"text": indexing, "tone": "info"})
        if lists:
            meta.append({"text": lists})
        if w.get("citations"):
            meta.append({"text": f"цитирований: {w['citations']}"})
        if w.get("doi"):
            meta.append({"text": f"DOI {w['doi']}", "href": _doi_url(w["doi"])})
        details = [
            *_row("Вид", kind),
            *_row("Журнал" if w["kind"] == "journal_article" else "Издание", w.get("venue")),
            *_row("Выходные данные", imprint),
            *_row("Издательство", w.get("publisher")),
            *_row("DOI", w.get("doi"), href=w.get("doi") and _doi_url(w["doi"])),
            *_row("ISSN", w.get("issn")),
            *_row("ISBN", w.get("isbn")),
            *_row("Индексация", indexing, tone="info"),
            *_row("Журнал в списках", lists),
            *_row("Цитирований", w.get("citations")),
        ]
    elif w["type"] == "talk":
        e = w["event"]
        dates = L.format_date_range(e["dateStart"], e.get("dateEnd")) if e.get("dateStart") else ""
        talk_kind = L.TALK_KIND.get(w.get("kind") or "other", "")
        byline = [{"label": "Докладчики:", "people": _links(w["presenters"])}]
        where = _join([e.get("name"), e.get("city"), dates], ", ")
        level = L.CONF_LEVEL.get(e.get("level") or "", "")
        if level:
            meta.append({"text": f"{level.lower()} конференция"})
        if w.get("award"):
            meta.append({"text": w["award"], "tone": "warn"})
        details = [
            *_row("Конференция", e.get("name"), href=e.get("url")),
            *_row("Уровень", level),
            *_row("Место и даты", _join([e.get("city"), e.get("country"), dates], ", ")),
            *_row("Вид доклада", talk_kind),
            *_row("Формат", L.TALK_FORMAT.get(w.get("format") or "")),
            *_row("Докладчики", "", people=_links(w["presenters"])),
            *_row("Награда", w.get("award"), tone="warn"),
        ]
    else:
        byline = [{"people": _links(w["authors"])}, {"label": "руководитель", "people": _links(w["supervisors"])}]
        d = w["direction"]
        direction = _join([d.get("code"), d.get("name")], " ")
        where = direction + (f", {w['discipline']}" if w.get("discipline") else "")
        details = [
            *_row("Руководители" if len(w["supervisors"]) > 1 else "Руководитель", "", people=_links(w["supervisors"])),
            *_row("Уровень", L.LEVEL.get(w.get("level") or "")),
            *_row("Направление", direction),
            *_row("Профиль", w.get("profile")),
            *_row("Дисциплина", w.get("discipline")),
            *_row("Страниц", w.get("pages")),
        ]
    if w["accessLevel"] == "card_only":
        meta.append({"text": "только описание"})
    is_open = w["accessLevel"] == "full" and w["hasFile"]
    subjects = w.get("subjects") or []
    view: dict[str, Any] = {
        "kind": kind,
        "authors": _links(w["authors"]),
        "byline": byline,
        "where": where,
        "meta": meta,
        "details": [
            *details,
            *_row("Год", w.get("year")),
            *_row("Кафедра", w.get("department")),
            *_row("Язык", L.LANGUAGE.get(w.get("language") or "")),
            *(
                row
                for scheme, label in L.SUBJECT_SCHEME.items()
                for row in _row(label, "; ".join(f"{s['code']} {s['name']}" for s in subjects if s["scheme"] == scheme))
            ),
        ],
        "materials": [
            {"id": m["id"], "label": f"{L.MATERIAL_KIND[m['kind']]}: {m['name']}, {L.format_size(m['size'])}"}
            for m in materials
        ],
    }
    if w["type"] in ("vkr", "coursework") and w.get("annotation"):
        view["snippet"] = w["annotation"]
    if is_open:
        view["fileButton"] = "Открыть материалы" if w["type"] == "talk" else "Открыть текст"
    if w["type"] == "publication" and w.get("doi"):
        view["external"] = {"href": _doi_url(w["doi"]), "label": "Открыть по DOI"}
    elif w.get("externalUrl"):
        view["external"] = {"href": w["externalUrl"], "label": "Страница издания"}
    if w["accessLevel"] == "card_only":
        view["closedNote"] = "Полный текст закрыт — доступна только карточка"
    return view
