import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI

from import_service.src.api import router
from import_service.src.dependencies import build_service
from shared.bus.consumer import MessageBusConsumer
from shared.bus.contracts import ImportTopic, ItemExtractedDTO
from shared.bus.outbox import OutboxRelay
from shared.bus.publisher import MessageBusPublisher
from shared.config import rabbit_config
from shared.logging import get_logger
from shared.middleware import AuthMiddleware, OriginCheckMiddleware, create_app
from shared.storage import storage_lifespan
from shared.uow import uow_write_factory

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with storage_lifespan(app):
        try:
            connection = await aio_pika.connect_robust(rabbit_config.url)
        except Exception:
            logger.exception("Сервис не стартовал: нет RabbitMQ")
            raise
        async with connection:
            publisher = MessageBusPublisher(
                await connection.channel()
            )  # publisher confirms: строка outbox удаляется после подтверждения
            await publisher.start()
            app.state.outbox = OutboxRelay(uow_write_factory, publisher)
            service = build_service(app)
            consumer_channel = await connection.channel()
            # Без лимита после рестарта весь бэклог разом ждал бы блокировку одного импорта и выел пул соединений базы
            await consumer_channel.set_qos(prefetch_count=4)
            consumer = MessageBusConsumer(
                consumer_channel,
                "import_service",
                {ImportTopic.ITEM_EXTRACTED: (ItemExtractedDTO, service.on_extracted)},
            )
            await consumer.start()
            relay = asyncio.create_task(app.state.outbox.run())
            try:
                yield
            finally:
                relay.cancel()


app = create_app("import_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware), lifespan=lifespan)
