"""Поиск публикации по DOI в Crossref (api.crossref.org/works/{doi}). Сервер ходит только по адресу из кода."""

import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from shared.config import app_config
from shared.exceptions import BadRequestError, NotFoundError, UpstreamError
from shared.logging import get_logger

logger = get_logger(__name__)

CROSSREF = "https://api.crossref.org/works/"
DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
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


def from_crossref(message: dict[str, Any]) -> dict[str, Any]:
    """Поля публикации из ответа Crossref; чего нет — не трогаем."""
    first = lambda key: (message.get(key) or [None])[0]  # noqa: E731
    issued = (message.get("issued") or {}).get("date-parts") or [[None]]
    title = first("title")
    out = {
        "title": re.sub(r"<[^>]+>", "", title) if title else None,  # JATS-разметка в заголовках
        "authors": [_author(a) for a in message.get("author") or []] or None,
        "venue": first("container-title"),
        "year": issued[0][0] if issued and issued[0] else None,
        "volume": message.get("volume"),
        "issue": message.get("issue"),
        "pages": (message.get("page") or "").replace("-", "–") or None,
        "issn": first("ISSN"),
        "kind": KINDS.get(message.get("type") or ""),
        "language": message.get("language") if message.get("language") in ("ru", "en") else None,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


async def lookup(doi: str) -> dict[str, Any]:
    """Crossref → поля; 404 — DOI не найден. httpx не следует редиректам, DOI в пути закодирован целиком."""
    agent = (
        f"faculty-repository (mailto:{app_config.crossref_mailto})"
        if app_config.crossref_mailto
        else "faculty-repository"
    )
    url = CROSSREF + quote(doi, safe="")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": agent}) as http:
            response = await http.get(url)
    except httpx.HTTPError as e:
        logger.warning("Crossref не ответил", extra={"url": url, "error": repr(e)})
        raise UpstreamError("Crossref не ответил, попробуйте позже") from e
    log = {"url": url, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000)}
    if response.status_code == 404:
        logger.info("Crossref: DOI не найден", extra=log)
        raise NotFoundError("DOI не найден", code="doi")
    if response.status_code != 200:
        logger.warning("Crossref ответил ошибкой", extra=log | {"body": response.text[:500]})
        raise UpstreamError("Crossref ответил ошибкой, попробуйте позже")
    try:
        message = response.json().get("message") or {}
    except ValueError as e:  # 200 с HTML-заглушкой вместо JSON
        logger.warning("Crossref ответил не JSON", extra=log | {"body": response.text[:500]})
        raise UpstreamError("Crossref ответил ошибкой, попробуйте позже") from e
    logger.info("Crossref", extra=log)
    return from_crossref(message)
