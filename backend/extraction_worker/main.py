"""Воркер без API: шина и /healthcheck для compose. PDF → поля карточки → import_item.extracted."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aio_pika
import httpx
from fastapi import FastAPI

from extraction_worker.extract import ItemError, LLMUnavailable, extract
from shared.bus.consumer import Delivery, MessageBusConsumer
from shared.bus.publisher import MessageBusPublisher
from shared.config import llm_config, rabbit_config
from shared.contracts import ImportTopic, ItemCreatedDTO, ItemExtractedDTO
from shared.exceptions import RetryMessageError
from shared.http import create_app
from shared.logging import get_logger
from shared.storage import Storage, storage_lifespan

logger = get_logger(__name__)

ITEM_TIMEOUT = 120
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 30


@dataclass(slots=True)
class ExtractionHandler:
    storage: Storage
    publisher: MessageBusPublisher
    http: httpx.AsyncClient

    async def __call__(self, dto: ItemCreatedDTO, delivery: Delivery) -> None:
        log = {"item_id": str(dto.item_id), "file_key": dto.file_key, "work_type": dto.work_type}
        result = ItemExtractedDTO(item_id=dto.item_id)
        started = time.perf_counter()
        if delivery.delivery_count >= 2:
            # третья доставка: файл, похоже, роняет воркер — иначе он крутился бы вечно
            logger.error("Файл роняет воркер — отдаём ошибку", extra=log | {"delivery_count": delivery.delivery_count})
            result.error = "Файл не удалось разобрать"
        else:
            try:
                async with asyncio.timeout(ITEM_TIMEOUT):
                    log["stage"] = "s3"  # extract двигает стадию дальше: итоговая строка скажет, где упало
                    pdf = await self.storage.get(dto.file_key)
                    logger.info("PDF получен", extra=log | {"bytes": len(pdf)})
                    result.fields, result.field_meta = await extract(pdf, self.http, log)
            except LLMUnavailable:
                if delivery.retries < MAX_RETRIES:
                    raise RetryMessageError("LLM-сервер недоступен") from None
                result.error = "ИИ не ответил"
            except TimeoutError:
                logger.warning("Элемент не уложился в таймаут", extra=log | {"timeout": ITEM_TIMEOUT})
                result.error = "ИИ не ответил"
            except ItemError as e:
                result.error = str(e)
        logger.log(
            logging.WARNING if result.error else logging.INFO,
            "Элемент разобран",
            extra=log | {"error": result.error, "duration_ms": round((time.perf_counter() - started) * 1000)},
        )
        await self.publisher.publish(ImportTopic.ITEM_EXTRACTED, result)


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
