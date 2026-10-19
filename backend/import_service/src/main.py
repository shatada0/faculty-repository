from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI

from import_service.src.api import router
from import_service.src.dependencies import build_service
from shared.bus.consumer import MessageBusConsumer
from shared.bus.contracts import ImportTopic, ItemExtractedDTO
from shared.bus.publisher import MessageBusPublisher
from shared.config import rabbit_config
from shared.logging import get_logger
from shared.middleware import AuthMiddleware, OriginCheckMiddleware, create_app
from shared.storage import storage_lifespan

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
            channel = await connection.channel()
            app.state.publisher = MessageBusPublisher(channel)
            await app.state.publisher.start()
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
            yield


app = create_app("import_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware), lifespan=lifespan)
