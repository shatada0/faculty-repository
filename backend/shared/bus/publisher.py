"""Публикация событий в topic-exchange events."""

from typing import Any
from uuid import uuid4

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange
from pydantic import BaseModel

from shared.bus.envelope import EXCHANGE, BusMessage
from shared.config import app_config
from shared.context import get_context
from shared.logging import get_logger

logger = get_logger(__name__)


async def declare_exchange(channel: AbstractChannel) -> AbstractExchange:
    return await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)


class MessageBusPublisher:
    """source — из конфига сервиса, trace_id и user_id — из контекста, сообщение persistent."""

    def __init__(self, channel: AbstractChannel) -> None:
        self._channel = channel

    async def start(self) -> None:
        self._exchange = await declare_exchange(self._channel)

    async def publish(
        self, topic: str, dto: BaseModel | dict[str, Any], *, trace_id: str | None = None, user_id: str | None = None
    ) -> None:
        """trace_id и user_id явно — у события из outbox: контекст запроса, который его записал, уже закончился."""
        if trace_id is None:
            ctx = get_context()
            trace_id, user_id = ctx.trace_id, ctx.user_id
        envelope = BusMessage(
            type=topic,
            source=app_config.service_name,
            trace_id=trace_id,
            user_id=user_id,
            payload=dto if isinstance(dto, dict) else dto.model_dump(mode="json"),
        )
        message = aio_pika.Message(
            envelope.model_dump_json().encode(),
            content_type="application/json",
            message_id=uuid4().hex,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            type=topic,
        )
        try:
            await self._exchange.publish(message, routing_key=topic)  # канал с publisher confirms: ждём подтверждения
        except Exception:
            logger.exception("Событие не опубликовано", extra={"topic": topic})
            raise
        logger.info("Событие опубликовано", extra={"topic": topic, "message_id": message.message_id})
