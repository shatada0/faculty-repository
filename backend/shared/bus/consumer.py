"""Потребление событий: маршрутизация по типу, контекст из конверта, dead letters и очередь повторов."""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from pydantic import BaseModel, ValidationError

from shared.bus.envelope import DEAD_EXCHANGE, DEAD_QUEUE, BusMessage
from shared.bus.publisher import declare_exchange
from shared.context import MessageContext, use_context
from shared.exceptions import RejectMessageError, RetryMessageError
from shared.logging import get_logger

logger = get_logger(__name__)

PAYLOAD_LOG_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class Delivery:
    redelivered: bool
    delivery_count: int  # x-delivery-count quorum-очереди: сколько раз сообщение уже выдавали
    retries: int  # сколько раз сообщение уже прошло через очередь повторов


Handler = Callable[[Any, Delivery], Awaitable[None]]


def _log_payload(payload: dict) -> str:
    # В fields — ФИО и аннотации: в лог не пишем.
    return json.dumps({k: v for k, v in payload.items() if k != "fields"}, ensure_ascii=False, default=str)[
        :PAYLOAD_LOG_LIMIT
    ]


class MessageBusConsumer:
    """Очередь сервиса с его именем. quorum=True — RabbitMQ считает доставки; retry_ttl — повтор через TTL-очередь."""

    def __init__(
        self,
        channel: AbstractChannel,
        queue: str,
        handlers: dict[str, tuple[type[BaseModel], Handler]],
        *,
        quorum: bool = False,
        retry_ttl_seconds: int | None = None,
    ) -> None:
        self._channel = channel
        self._queue_name = queue
        self._handlers = handlers
        self._quorum = quorum
        self._retry_ttl = retry_ttl_seconds

    @property
    def retry_queue(self) -> str:
        return f"{self._queue_name}.retry"

    async def start(self) -> None:
        """Объявляет топологию и подписывается. Не блокирует: вызывается в lifespan."""
        exchange = await declare_exchange(self._channel)
        dead = await self._channel.declare_exchange(DEAD_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
        dead_queue = await self._channel.declare_queue(DEAD_QUEUE, durable=True)
        await dead_queue.bind(dead)
        args: dict[str, Any] = {"x-dead-letter-exchange": DEAD_EXCHANGE}
        if self._quorum:
            args["x-queue-type"] = "quorum"
        queue = await self._channel.declare_queue(self._queue_name, durable=True, arguments=args)
        for topic in self._handlers:
            await queue.bind(exchange, routing_key=topic)
        if self._retry_ttl:
            # Истёкшее сообщение возвращается в основную очередь через default exchange, RabbitMQ пишет x-death.
            await self._channel.declare_queue(
                self.retry_queue,
                durable=True,
                arguments={
                    "x-message-ttl": self._retry_ttl * 1000,
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": self._queue_name,
                },
            )
        await queue.consume(self._on_message)

    @staticmethod
    def _retries(message: AbstractIncomingMessage) -> int:
        # Свой счётчик, а не x-death: RabbitMQ 4 не доверяет x-заголовкам, которые переопубликовал клиент.
        return int(message.headers.get("retries") or 0)

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        log = {"queue": self._queue_name, "message_id": message.message_id, "redelivered": message.redelivered}
        try:
            envelope = BusMessage.model_validate_json(message.body)
        except ValidationError:
            logger.warning("Битый конверт — в dead letters", extra=log)
            await message.reject(requeue=False)
            return
        log |= {"type": envelope.type, "source": envelope.source, "payload": _log_payload(envelope.payload)}
        with use_context(MessageContext(trace_id=envelope.trace_id, user_id=envelope.user_id)):
            route = self._handlers.get(envelope.type)
            if route is None:
                logger.warning("Неизвестный тип события — в dead letters", extra=log)
                await message.reject(requeue=False)
                return
            dto_type, handler = route
            try:
                dto = dto_type.model_validate(envelope.payload)
            except ValidationError as e:
                logger.warning(
                    "Payload не прошёл валидацию — в dead letters",
                    extra=log | {"errors": e.errors(include_input=False)},
                )
                await message.reject(requeue=False)
                return
            delivery = Delivery(
                message.redelivered, int(message.headers.get("x-delivery-count") or 0), self._retries(message)
            )
            logger.info("Событие получено", extra=log)
            try:
                await handler(dto, delivery)
            except RetryMessageError as e:
                if not self._retry_ttl:
                    # raise отсюда прошёл бы мимо соседних except: сообщение висело бы без ack до закрытия канала
                    logger.warning(
                        "Временная ошибка без очереди повторов — в dead letters", extra=log | {"reason": str(e)}
                    )
                    await message.reject(requeue=False)
                    return
                # Джиттер 0–½ TTL до переотправки: у очереди повторов TTL общий, и пачка, упавшая разом (LLM лёг),
                # иначе вернулась бы тоже разом. Пока ждём, сообщение без ack держит слот prefetch — это и нужно.
                pause = random.uniform(0, self._retry_ttl / 2)  # noqa: S311 — джиттер, не криптография
                logger.warning(
                    "Временная ошибка — повтор через очередь",
                    extra=log | {"reason": str(e), "retries": delivery.retries, "jitter_s": round(pause, 1)},
                )
                await asyncio.sleep(pause)
                await self._channel.default_exchange.publish(
                    aio_pika.Message(
                        message.body,
                        headers={"retries": delivery.retries + 1},
                        message_id=message.message_id,
                        content_type=message.content_type,
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=self.retry_queue,
                )
                await message.ack()
                return
            except RejectMessageError as e:
                logger.warning("Сообщение отклонено — в dead letters", extra=log | {"reason": str(e)})
                await message.reject(requeue=False)
                return
            except Exception:
                logger.exception("Обработчик упал — в dead letters", extra=log)
                await message.reject(requeue=False)
                return
            await message.ack()
