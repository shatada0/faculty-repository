"""Воркер без API: шина и /healthcheck для compose. PDF → поля карточки → import_item.extracted."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
import httpx
from fastapi import FastAPI

from extraction_worker.src.service import ExtractionHandler
from extraction_worker.src.service.extraction_service import RETRY_DELAY_SECONDS
from shared.bus.consumer import MessageBusConsumer
from shared.bus.contracts import ImportTopic, ItemCreatedDTO
from shared.bus.publisher import MessageBusPublisher
from shared.config import llm_config, rabbit_config
from shared.logging import get_logger
from shared.middleware import create_app
from shared.storage import storage_lifespan

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with storage_lifespan(app), httpx.AsyncClient() as http:  # httpx не следует редиректам
        try:
            connection = await aio_pika.connect_robust(rabbit_config.url)
        except Exception:
            logger.exception("Воркер не стартовал: нет RabbitMQ")
            raise
        async with connection:
            publisher = MessageBusPublisher(await connection.channel())
            await publisher.start()
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=llm_config.concurrency)  # столько параллельных запросов тянет LLM
            handler = ExtractionHandler(app.state.storage, publisher, http)
            consumer = MessageBusConsumer(
                channel,
                "extraction_worker",
                {ImportTopic.ITEM_CREATED: (ItemCreatedDTO, handler)},
                quorum=True,
                retry_ttl_seconds=RETRY_DELAY_SECONDS,
            )
            await consumer.start()
            yield


app = create_app("extraction_worker", [], lifespan=lifespan)
