"""Проверки черновика: errors (что мешает публикации), issues (на что смотреть), actions, lowConfidence.

Черновик сохраняется неполным, публикация требует этих полей. Эталон сообщений — src/mocks/schemas.ts.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from shared.works import Work
from shared.works import labels as L
from shared.works.schemas import material_dict, work_dict

LOW_CONFIDENCE = 0.7
ACTIONS = {"draft": ["publish", "reject"], "published": ["unpublish", "reject"], "rejected": ["publish"]}


def _fail(message: str) -> PydanticCustomError:
    return PydanticCustomError("check", message)


def blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def need(message: str) -> AfterValidator:
    def check(v: Any) -> Any:
        if blank(v):
            raise _fail(message)
        return v

    return AfterValidator(check)


def nonempty(message: str) -> AfterValidator:
    def check(v: list) -> list:
        if not v:
            raise _fail(message)
        return v

    return AfterValidator(check)


def _year(v: int | None) -> int | None:
    max_year = datetime.now(UTC).year + 1
    if v is None:
        raise _fail("Укажите год")
    if v < 1990:
        raise _fail("Год не раньше 1990")
    if v > max_year:
        raise _fail(f"Год не позже {max_year}")
    return v


class Check(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Ref(Check):
    displayName: Annotated[str | None, need("Укажите ФИО")] = None


class Common(Check):
    title: Annotated[str | None, need("Укажите название")] = None
    authors: Annotated[list[Ref], nonempty("Добавьте хотя бы одного автора")] = []
    year: Annotated[int | None, AfterValidator(_year)] = None


class Direction(Check):
    code: str | None = None

    @field_validator("code")
    @classmethod
    def code_format(cls, v: str | None) -> str | None:
        if not v or not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", v.strip()):
            raise _fail("Код направления в формате NN.NN.NN")
        return v


class Student(Common):
    supervisors: Annotated[list[Ref], nonempty("Укажите научного руководителя")] = []
    level: Annotated[str | None, need("Выберите уровень")] = None
    direction: Direction
    department: Annotated[str | None, need("Укажите кафедру")] = None
    pages: int | None = None
    consent: bool = False
    accessLevel: str  # после consent: проверка читает его из info.data

    @field_validator("pages")
    @classmethod
    def positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise _fail("Число страниц больше нуля")
        return v

    @field_validator("accessLevel")
    @classmethod
    def consent_for_full(cls, v: str, info: ValidationInfo) -> str:
        if v == "full" and not info.data.get("consent"):
            raise _fail("Нет согласия студента на открытый доступ к тексту")
        return v


class Publication(Common):
    kind: Annotated[str | None, need("Выберите вид публикации")] = None
    venue: str | None = None
    publisher: str | None = None
    doi: str | None = None

    @field_validator("venue")
    @classmethod
    def venue_needed(cls, v: str | None, info: ValidationInfo) -> str | None:
        if info.data.get("kind") not in ("monograph", "textbook") and blank(v):
            raise _fail("Укажите журнал или сборник")
        return v

    @field_validator("publisher")
    @classmethod
    def publisher_needed(cls, v: str | None, info: ValidationInfo) -> str | None:
        if info.data.get("kind") in ("monograph", "textbook") and blank(v):
            raise _fail("Укажите издательство")
        return v

    @field_validator("doi")
    @classmethod
    def doi_format(cls, v: str | None) -> str | None:
        if v and not re.fullmatch(r"10\.\d{4,9}/\S+", v.strip()):
            raise _fail("DOI в формате 10.xxxx/...")
        return v


class Event(Check):
    name: Annotated[str | None, need("Укажите конференцию")] = None
    level: Annotated[str | None, need("Выберите уровень конференции")] = None
    dateStart: Annotated[str | None, need("Укажите дату начала")] = None
    dateEnd: str | None = None

    @field_validator("dateEnd")
    @classmethod
    def after_start(cls, v: str | None, info: ValidationInfo) -> str | None:
        start = info.data.get("dateStart")
        if v and start and v < start:
            raise _fail("Дата окончания не раньше даты начала")
        return v


class Talk(Common):
    event: Event
    presenters: Annotated[list[Ref], nonempty("Отметьте хотя бы одного докладчика")] = []


CHECKS: dict[str, type[Check]] = {"vkr": Student, "coursework": Student, "publication": Publication, "talk": Talk}


def errors(data: dict[str, Any]) -> list[dict]:
    """Что мешает публикации: [{field: "event.dateEnd", message}] — путь как loc у pydantic."""
    try:
        CHECKS[data["type"]].model_validate(data)
    except ValidationError as e:
        return [{"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]} for err in e.errors()]
    return []


def unsure(meta: dict) -> bool:
    return (
        meta.get("source") == "ai"
        and (meta.get("confidence") if meta.get("confidence") is not None else 1) < LOW_CONFIDENCE
    )


def unmatched_names(data: dict[str, Any]) -> list[str]:
    refs = [*data["authors"], *data.get("supervisors", []), *data.get("presenters", [])]
    return list(dict.fromkeys(a["displayName"] for a in refs if not a.get("personId") and a.get("displayName")))


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or (isinstance(v, dict) and all(_empty(x) for x in v.values()))


def live_meta(data: dict[str, Any], field_meta: dict) -> dict:
    """Источник только у заполненного поля: кафедру не из справочника импорт стирает, а «ИИ, 85%» оставалось бы."""
    return {k: m for k, m in field_meta.items() if not (k in data and _empty(data[k]))}


def issues(data: dict[str, Any], errs: list[dict], field_meta: dict) -> list[str]:
    return [
        *(e["message"] for e in errs),
        *(f"ИИ не уверен: {L.FIELD.get(k, k)}" for k, m in field_meta.items() if unsure(m)),
        *(f"Не распознан автор: {n}" for n in unmatched_names(data)),
    ]


def admin_dict(w: Work, journal_lists: list[str] | None = None) -> dict[str, Any]:
    """AdminWork: все поля (None — пусто) плюс то, что сервер считает при каждом ответе; при записи игнорируется."""
    data = work_dict(w, journal_lists)
    meta = live_meta(data, w.field_meta)
    field_meta = {k: m | {"lowConfidence": True} if unsure(m) else m for k, m in meta.items()}
    errs = errors(data)
    return data | {
        "status": w.status,
        "importId": str(w.import_id) if w.import_id else None,
        "originalFileName": w.original_file_name,
        "fieldMeta": field_meta,
        "rejectReason": w.reject_reason,
        "duplicateOf": str(w.duplicate_of) if w.duplicate_of else None,
        "createdAt": w.created_at.isoformat(),
        "updatedAt": w.updated_at.isoformat(),
        "importFileName": w.import_.file_name if w.import_ else None,
        "errors": errs,
        "issues": issues(data, errs, meta),
        "actions": ACTIONS[w.status],
        "materials": [material_dict(m, m.uploader.name if m.uploader else "") for m in w.materials],
    }


def check_work(w: Work, **overrides: Any) -> tuple[list[dict], list[str]]:
    """errors и issues без загрузки материалов и импорта: для очереди, сводки и bulk."""
    data = work_dict(w) | overrides
    errs = errors(data)
    return errs, issues(data, errs, live_meta(data, w.field_meta))
