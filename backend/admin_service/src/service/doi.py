"""Crossref (api.crossref.org/works) по DOI и список работ из ORCID (pub.orcid.org). Сервер ходит только по адресам из кода."""

import contextlib
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from shared.config import app_config
from shared.exceptions import BadRequestError, NotFoundError, UpstreamError
from shared.logging import get_logger
from shared.works.schemas import issn_key

logger = get_logger(__name__)

CROSSREF = "https://api.crossref.org/works/"
ORCID_API = "https://pub.orcid.org/v3.0"
BATCH = 50  # столько DOI фильтр Crossref берёт одним запросом, как в citations
DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
KINDS = {
    "journal-article": "journal_article",
    "proceedings-article": "proceedings_paper",
    "book-chapter": "book_chapter",
    "monograph": "monograph",
    "book": "monograph",
    "posted-content": "other",
}


def normalize_doi(raw: str) -> str:
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw.strip().lower())
    if not DOI_RE.fullmatch(doi):
        raise BadRequestError("Введите DOI в формате 10.xxxx/...", code="doi")
    return doi


def _author(a: dict) -> str:
    """«Орлов Д. С.» из given/family Crossref."""
    initials = " ".join(f"{part[0]}." for part in re.split(r"[\s.\-]+", a.get("given") or "") if part)
    return " ".join(x for x in (a.get("family") or a.get("name") or "", initials) if x)


def _orcid(a: dict) -> str | None:
    """«https://orcid.org/0000-0003-2071-2163» → «0000-0003-2071-2163»."""
    m = ORCID_RE.search(a.get("ORCID") or "")
    return m[0] if m else None


def from_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """Поля публикации из ответа Crossref; чего нет — не трогаем.

    author_orcids — ORCID авторов по порядку (None, где его нет); citations — is-referenced-by-count;
    issns — все ISSN, печатный первым: списки ВАК и «белый» обычно ведут по нему, а справочник может знать любой.
    """
    first = lambda key: (message.get(key) or [None])[0]  # noqa: E731
    issued = (message.get("issued") or {}).get("date-parts") or [[None]]
    title = first("title")
    title = re.sub(r"<[^>]+>", "", title) if title else None  # JATS-разметка в заголовках
    authors = message.get("author") or []
    printed = [t.get("value") for t in message.get("issn-type") or [] if t.get("type") == "print"]
    issns = list(dict.fromkeys(issn_key(i) for i in [*printed, *(message.get("ISSN") or [])] if i))
    language = message.get("language")
    if (
        language not in ("ru", "en") and title
    ):  # Crossref часто не знает язык: шаблонное «ru» осталось бы у английской статьи
        language = "ru" if CYRILLIC.search(title) else "en"
    out = {
        "title": title,
        "authors": [_author(a) for a in authors] or None,
        "author_orcids": [_orcid(a) for a in authors] if any(_orcid(a) for a in authors) else None,
        "venue": first("container-title"),
        "year": issued[0][0] if issued and issued[0] else None,
        "volume": message.get("volume"),
        "issue": message.get("issue"),
        # у статей без страниц (Physical Review, MDPI) — номер статьи
        "pages": (message.get("page") or message.get("article-number") or "").replace("-", "–") or None,
        "issn": issns[0] if issns else None,
        "issns": issns if len(issns) > 1 else None,
        "kind": KINDS.get(message.get("type") or ""),
        "language": language if language in ("ru", "en") else None,
        "citations": message.get("is-referenced-by-count"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


async def _get(source: str, url: str, params: dict | None = None, headers: dict | None = None) -> dict | None:
    """GET JSON с логом и понятной ошибкой; 404 → None. httpx не следует редиректам."""
    agent = (
        f"faculty-repository (mailto:{app_config.crossref_mailto})"
        if app_config.crossref_mailto
        else "faculty-repository"
    )
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": agent, **(headers or {})}) as http:
            response = await http.get(url, params=params)
    except httpx.HTTPError as e:
        logger.warning(f"{source} не ответил", extra={"url": url, "error": repr(e)})
        raise UpstreamError(f"{source} не ответил, попробуйте позже") from e
    log = {"url": url, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000)}
    if response.status_code == 404:
        logger.info(f"{source}: не найдено", extra=log)
        return None
    if response.status_code != 200:
        logger.warning(f"{source} ответил ошибкой", extra=log | {"body": response.text[:500]})
        raise UpstreamError(f"{source} ответил ошибкой, попробуйте позже")
    try:
        body = response.json()
    except ValueError as e:  # 200 с HTML-заглушкой вместо JSON
        logger.warning(f"{source} ответил не JSON", extra=log | {"body": response.text[:500]})
        raise UpstreamError(f"{source} ответил ошибкой, попробуйте позже") from e
    logger.info(source, extra=log)
    return body


async def lookup(doi: str) -> dict[str, Any]:
    """Crossref → поля; 404 — DOI не найден. DOI в пути закодирован целиком."""
    body = await _get("Crossref", CROSSREF + quote(doi, safe=""))
    if body is None:
        raise NotFoundError("DOI не найден", code="doi")
    return from_crossref(body.get("message") or {})


async def lookup_many(dois: list[str]) -> dict[str, dict[str, Any]]:
    """Много DOI фильтром Crossref по BATCH за запрос → {doi: поля}; чего Crossref не знает, в ответе нет.
    «,» — разделитель фильтра: такие DOI (старые SICI у Wiley) спрашиваются по одному."""
    out: dict[str, dict[str, Any]] = {}
    plain = [d for d in dois if "," not in d]
    for i in range(0, len(plain), BATCH):
        chunk = plain[i : i + BATCH]
        params = {"filter": ",".join(f"doi:{d}" for d in chunk), "rows": len(chunk)}
        body = await _get("Crossref", CROSSREF.rstrip("/"), params) or {}
        for m in (body.get("message") or {}).get("items") or []:
            if m.get("DOI"):
                out[m["DOI"].lower()] = from_crossref(m)
    for d in dois:
        if "," in d:
            with contextlib.suppress(NotFoundError):
                out[d] = await lookup(d)
    return out


async def orcid_dois(orcid: str) -> list[str]:
    """DOI работ из публичного профиля ORCID, по порядку профиля без повторов.

    Только relationship «self»: «part-of» — DOI книги или сборника, в который входит работа, а не её собственный.
    """
    body = await _get("ORCID", f"{ORCID_API}/{orcid}/works", headers={"Accept": "application/json"})
    if body is None:
        raise NotFoundError("Профиль ORCID не найден", code="orcid")
    dois = []
    for group in body.get("group") or []:
        for ext in ((group.get("external-ids") or {}).get("external-id")) or []:
            if ext.get("external-id-type") != "doi" or ext.get("external-id-relationship", "self") != "self":
                continue
            value = ext.get("external-id-value") or ""
            if m := DOI_RE.search(value.lower()):
                dois.append(m[0].rstrip("."))
    return list(dict.fromkeys(dois))
