"""Публикация событий в topic-exchange events."""

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

    async def publish(self, topic: str, dto: BaseModel) -> None:
        ctx = get_context()
        envelope = BusMessage(
            type=topic,
            source=app_config.service_name,
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            payload=dto.model_dump(mode="json"),
        )
        message = aio_pika.Message(
            envelope.model_dump_json().encode(),
            content_type="application/json",
            message_id=uuid4().hex,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            type=topic,
        )
        try:
            await self._exchange.publish(message, routing_key=topic)
        except Exception:
            # ponytail: публикация после коммита упала — событие потеряно; transactional outbox, когда это станет важно
            logger.exception("Событие не опубликовано", extra={"topic": topic})
            raise
        logger.info("Событие опубликовано", extra={"topic": topic, "message_id": message.message_id})
