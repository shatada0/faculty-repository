"""Transactional outbox: событие не теряется, если шина недоступна или сервис упал между коммитом и публикацией.

enqueue() — в транзакции вместе с данными; OutboxRelay.run() — фоном в сервисе: публикует и удаляет строки.
Доставка «хотя бы раз»: упали между подтверждением шины и удалением строки — событие уйдёт повторно,
поэтому потребители идемпотентны (on_extracted обрабатывает только pending, воркер просто разберёт файл ещё раз).
"""

import asyncio
import random

from pydantic import BaseModel
from sqlalchemy import select

from shared.bus.models import OutboxMessage
from shared.bus.publisher import MessageBusPublisher
from shared.context import get_context
from shared.logging import get_logger
from shared.uow import UnitOfWork, UnitOfWorkFactory

logger = get_logger(__name__)
BATCH = 100
INTERVAL_SECONDS = 5  # без kick() — так часто: шина вернулась, другой экземпляр сервиса упал до публикации


def enqueue(uow: UnitOfWork, topic: str, dto: BaseModel) -> None:
    ctx = get_context()
    uow.session.add(
        OutboxMessage(topic=topic, payload=dto.model_dump(mode="json"), trace_id=ctx.trace_id, user_id=ctx.user_id)
    )


class OutboxRelay:
    def __init__(self, uow_factory: UnitOfWorkFactory, publisher: MessageBusPublisher) -> None:
        self.uow_factory = uow_factory
        self.publisher = publisher
        self._wake = asyncio.Event()

    def kick(self) -> None:
        """После коммита: опубликовать сейчас, а не через INTERVAL_SECONDS."""
        self._wake.set()

    async def flush(self) -> bool:
        """Публикует всё, что есть. False — шина не приняла событие: остальное ждёт следующего прохода.

        SKIP LOCKED: несколько экземпляров сервиса не публикуют одну строку дважды.
        """
        while True:
            async with self.uow_factory() as uow:
                rows = (
                    await uow.session.execute(
                        select(OutboxMessage).order_by(OutboxMessage.id).limit(BATCH).with_for_update(skip_locked=True)
                    )
                ).scalars()
                done = 0
                for row in rows:
                    try:
                        await self.publisher.publish(row.topic, row.payload, trace_id=row.trace_id, user_id=row.user_id)
                    except Exception:  # publisher уже записал причину в лог; опубликованные строки удалятся коммитом
                        return False
                    await uow.session.delete(row)
                    done += 1
            if done < BATCH:
                return True

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.flush()
            except Exception:  # база недоступна — следующий проход
                logger.exception("Outbox не опубликован")
            try:
                # ±20 %: экземпляры сервиса не ходят в outbox синхронно
                async with asyncio.timeout(INTERVAL_SECONDS * random.uniform(0.8, 1.2)):  # noqa: S311
                    await self._wake.wait()
            except TimeoutError:
                pass
