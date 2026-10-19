"""Работа в форме контракта (раздел 4) и обратно: чтение строки works и схемы записи редактора и импорта."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

from shared.works import STUDENT_TYPES, Material, Work, WorkPerson, WorkSubject

PersonKind = Literal["staff", "student", "graduate", "external"]
ROLE_FIELDS = {"authors": "author", "supervisors": "supervisor", "presenters": "presenter"}
# Поля, которые ставит сервер: в теле запроса их не трогаем (редактор присылает форму целиком).
READ_ONLY = frozenset(
    {
        "id",
        "type",
        "status",
        "createdAt",
        "updatedAt",
        "importId",
        "importFileName",
        "publishedAt",
        "duplicateOf",
        "rejectReason",
        "originalFileName",
        "hasFile",
        "fieldMeta",
        "errors",
        "issues",
        "actions",
        "materials",
        "citations",
        "journalLists",
        "thumbnailUrl",
        "view",
    }
)
COLUMNS = {
    "title": "title",
    "year": "year",
    "department": "department",
    "annotation": "annotation",
    "keywords": "keywords",
    "language": "language",
    "accessLevel": "access_level",
    "externalUrl": "external_url",
    "consent": "consent",
    "doi": "doi",
    "issn": "issn",
}
RELATED = {"publication": "relatedTalkId", "talk": "relatedPublicationId"}


def title_key(title: str) -> str:
    """Название без регистра и знаков: по (type, title_key, year) импорт ищет дубль."""
    return re.sub(r"[^\w]+|_", " ", title.lower().replace("ё", "е")).strip()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def refs(w: Work, role: str) -> list[dict]:
    return [
        {"personId": str(p.person_id) if p.person_id else None, "displayName": p.display_name, "kind": p.kind}
        for p in w.refs(role)
    ]


def citations(w: Work) -> int | None:
    return max((c.count for c in w.citations), default=None)


def material_dict(m: Material, uploader: str) -> dict:
    return {
        "id": str(m.id),
        "name": m.name,
        "size": m.size,
        "kind": m.kind,
        "status": m.status,
        "uploadedBy": uploader,
        "uploadedAt": iso(m.uploaded_at),
        "rejectReason": m.reject_reason,
    }


def work_dict(w: Work, journal_lists: list[str] | None = None) -> dict[str, Any]:
    """Работа как в контракте: все поля, None — пусто. Публичный ответ потом выкидывает None, админский — нет."""
    d = w.details
    out: dict[str, Any] = {
        "id": str(w.id),
        "type": w.type,
        "title": w.title,
        "authors": refs(w, "author"),
        "year": w.year,
        "department": w.department,
        "annotation": w.annotation,
        "keywords": list(w.keywords),
        "language": w.language,
        "accessLevel": w.access_level,
        "hasFile": w.file_key is not None,
        "externalUrl": w.external_url,
        "publishedAt": iso(w.published_at),
        "subjects": [{"scheme": s.scheme, "code": s.code, "name": s.classifier.name} for s in w.subjects],
    }
    if w.type in STUDENT_TYPES:
        direction = d.get("direction") or {}
        out |= {
            "supervisors": refs(w, "supervisor"),
            "level": d.get("level"),
            "direction": {"code": direction.get("code"), "name": direction.get("name")},
            "profile": d.get("profile"),
            "discipline": d.get("discipline"),
            "pages": d.get("pages"),
            "consent": w.consent,
        }
        if w.type == "coursework":
            out["kind"] = d.get("kind")
    elif w.type == "publication":
        out |= {
            "kind": d.get("kind"),
            "venue": d.get("venue"),
            "volume": d.get("volume"),
            "issue": d.get("issue"),
            "pages": d.get("pages"),
            "publisher": d.get("publisher"),
            "doi": w.doi,
            "isbn": d.get("isbn"),
            "issn": w.issn,
            "indexing": d.get("indexing") or [],
            "relatedTalkId": str(w.related_id) if w.related_id else None,
            "citations": citations(w),
            "journalLists": journal_lists,
        }
    else:
        event = d.get("event") or {}
        out |= {
            "event": {k: event.get(k) for k in ("name", "level", "city", "country", "dateStart", "dateEnd", "url")},
            "kind": d.get("kind"),
            "format": d.get("format"),
            "presenters": refs(w, "presenter"),
            "award": d.get("award"),
            "relatedPublicationId": str(w.related_id) if w.related_id else None,
        }
    return out


def drop_none(value: Any) -> Any:
    """Публичные ответы — без пустых полей, как в моках (optional в типах фронта)."""
    if isinstance(value, dict):
        return {k: drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_none(v) for v in value]
    return value


def template(work_type: str, year: int) -> dict[str, Any]:
    """Новая работа со значениями по умолчанию: с неё начинается редактор."""
    student = work_type in STUDENT_TYPES
    base = {
        "type": work_type,
        "title": "",
        "authors": [{"displayName": "", "kind": "student" if student else "staff"}],
        "year": year,
        "department": "",
        "annotation": "",
        "keywords": [],
        "language": "ru",
        "hasFile": False,
        "externalUrl": "",
        "subjects": [],
    }
    if student:
        return base | {
            "accessLevel": "card_only",
            "supervisors": [{"displayName": "", "kind": "staff"}],
            "level": "bachelor",
            "direction": {"code": "", "name": ""},
            "profile": "",
            "discipline": "",
            "pages": None,
            "consent": False,
            **({"kind": "work"} if work_type == "coursework" else {}),
        }
    if work_type == "publication":
        return base | {
            "accessLevel": "card_only",
            "kind": "journal_article",
            "venue": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "publisher": "",
            "doi": "",
            "isbn": "",
            "issn": "",
            "indexing": [],
            "relatedTalkId": "",
        }
    return base | {
        "accessLevel": "full",
        "event": {
            "name": "",
            "level": "national",
            "city": "",
            "country": "Россия",
            "dateStart": "",
            "dateEnd": "",
            "url": "",
        },
        "kind": "section",
        "format": "offline",
        "presenters": [],
        "award": "",
        "relatedPublicationId": "",
    }


# ---------- схемы записи: редактор, импорт, DOI ----------


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, dict):
        return {k: _blank_to_none(v) for k, v in value.items()}
    return value


_HTTP_URL = TypeAdapter(HttpUrl)


def _url(value: Any) -> str | None:
    """Только http и https: javascript: из импорта не станет ссылкой в <a href>."""
    if value is None:
        return None
    try:
        return str(_HTTP_URL.validate_python(value))
    except ValidationError:
        raise PydanticCustomError("url", "Ссылка должна начинаться с http:// или https://") from None


Url = Annotated[str | None, BeforeValidator(_url)]
Text = Annotated[str | None, Field(max_length=20000)]
Short = Annotated[str | None, Field(max_length=1000)]


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def blanks(cls, data: Any) -> Any:
        return _blank_to_none(data)  # форма присылает '' вместо null


class AuthorRefIn(WriteModel):
    personId: UUID | None = None
    displayName: Short = None
    kind: PersonKind = "external"


class SubjectIn(WriteModel):
    scheme: Literal["grnti", "oecd", "wos", "asjc"]
    code: str = Field(max_length=50)
    name: str | None = None  # подпись берётся из справочника


class CommonIn(WriteModel):
    title: Short = None
    authors: list[AuthorRefIn] = Field(default_factory=list, max_length=200)
    year: int | None = Field(None, ge=0, le=9999)
    department: Short = None
    annotation: Text = None
    keywords: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=50)
    language: Literal["ru", "en", "other"] = "ru"
    accessLevel: Literal["full", "card_only", "hidden"] = "card_only"
    externalUrl: Url = None
    subjects: list[SubjectIn] = Field(default_factory=list, max_length=50)


class DirectionIn(WriteModel):
    code: Short = None
    name: Short = None


class StudentIn(CommonIn):
    supervisors: list[AuthorRefIn] = Field(default_factory=list, max_length=10)
    level: Literal["bachelor", "specialist", "master"] | None = None
    direction: DirectionIn = Field(default_factory=DirectionIn)
    profile: Short = None
    discipline: Short = None
    pages: int | None = Field(None, ge=0, le=100000)
    consent: bool = False


class CourseworkIn(StudentIn):
    kind: Literal["work", "project"] | None = "work"


class PublicationIn(CommonIn):
    kind: (
        Literal["journal_article", "proceedings_paper", "abstract", "book_chapter", "monograph", "textbook", "other"]
        | None
    ) = None
    venue: Short = None
    volume: Short = None
    issue: Short = None
    pages: Short = None
    publisher: Short = None
    doi: Short = None
    isbn: Short = None
    issn: Short = None
    indexing: list[Literal["scopus", "wos", "rinc", "vak", "core_rinc"]] = Field(default_factory=list)
    relatedTalkId: UUID | None = None


class EventIn(WriteModel):
    name: Short = None
    level: Literal["international", "national", "regional", "university"] | None = None
    city: Short = None
    country: Short = None
    dateStart: date | None = None
    dateEnd: date | None = None
    url: Url = None


class TalkIn(CommonIn):
    accessLevel: Literal["full", "card_only", "hidden"] = "full"
    event: EventIn = Field(default_factory=EventIn)
    kind: Literal["plenary", "section", "poster", "invited", "other"] | None = None
    format: Literal["offline", "online", "hybrid"] | None = None
    presenters: list[AuthorRefIn] = Field(default_factory=list, max_length=50)
    award: Short = None
    relatedPublicationId: UUID | None = None


WRITE_MODELS: dict[str, type[CommonIn]] = {
    "vkr": StudentIn,
    "coursework": CourseworkIn,
    "publication": PublicationIn,
    "talk": TalkIn,
}


def parse_write(work_type: str, body: dict[str, Any]) -> CommonIn:
    """Тело редактора → схема записи типа. Серверные поля молча отбрасываются, незнакомые — 422."""
    return WRITE_MODELS[work_type].model_validate({k: v for k, v in body.items() if k not in READ_ONLY})


def comparable(model: CommonIn) -> dict[str, Any]:
    """Значения в одном виде для сравнения «было — стало» (fieldMeta)."""
    return model.model_dump(mode="json")


def apply_write(w: Work, model: CommonIn) -> None:
    """Записывает присланные поля в строку и связанные таблицы. Пересчёт search и связей — в сервисе."""
    data = model.model_dump(mode="json", exclude_unset=True)
    details = dict(w.details)
    for key, value in data.items():
        if key in COLUMNS:
            setattr(w, COLUMNS[key], value or "" if key == "title" else value)
        elif key in ROLE_FIELDS:
            set_refs(w, ROLE_FIELDS[key], value)
        elif key == "subjects":
            set_subjects(w, value)
        elif key == RELATED.get(w.type):
            w.related_id = UUID(value) if value else None
        else:
            details[key] = value
    w.details = details
    w.title_key = title_key(w.title or "")  # новая работа без названия: default="" колонки подставит только INSERT


def set_refs(w: Work, role: str, items: list[dict]) -> None:
    """Строки обновляются на месте по позиции: удалить и вставить тот же PK в одном flush нельзя."""
    current = w.refs(role)
    for i, ref in enumerate(items):
        values = {
            "person_id": UUID(ref["personId"]) if ref.get("personId") else None,
            "display_name": (ref.get("displayName") or "").strip(),
            "kind": ref.get("kind") or "external",
        }
        if i < len(current):
            for k, v in values.items():
                setattr(current[i], k, v)
        else:
            w.people.append(WorkPerson(role=role, position=i, **values))
    for extra in current[len(items) :]:
        w.people.remove(extra)


def set_subjects(w: Work, items: list[dict]) -> None:
    wanted = {(s["scheme"], s["code"]) for s in items}
    for s in [s for s in w.subjects if (s.scheme, s.code) not in wanted]:
        w.subjects.remove(s)
    have = {(s.scheme, s.code) for s in w.subjects}
    for scheme, code in sorted(wanted - have):
        w.subjects.append(WorkSubject(scheme=scheme, code=code))
