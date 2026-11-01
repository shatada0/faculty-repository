"""События шины. Routing key — факт в домене, а не адресат."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ImportTopic(StrEnum):
    ITEM_CREATED = "import_item.created"
    ITEM_EXTRACTED = "import_item.extracted"


class ItemCreatedDTO(BaseModel):
    """PDF из MinIO (file_key) или запись списка текстом (text)."""

    item_id: UUID
    file_key: str | None = None
    work_type: str
    text: str | None = None


class ItemExtractedDTO(BaseModel):
    """Либо fields + field_meta, либо error."""

    item_id: UUID
    fields: dict[str, Any] | None = None
    field_meta: dict[str, dict[str, Any]] | None = None
    error: str | None = None
