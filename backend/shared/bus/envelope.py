"""Конверт сообщения шины: JSON через pydantic, без pickle."""

from typing import Any

from pydantic import BaseModel

EXCHANGE = "events"
DEAD_EXCHANGE = "events.dead"
DEAD_QUEUE = "dead_letters"


class BusMessage(BaseModel):
    type: str  # routing key — факт в домене: import_item.created
    source: str  # кто опубликовал: import-service
    trace_id: str
    user_id: str | None = None
    payload: dict[str, Any]
