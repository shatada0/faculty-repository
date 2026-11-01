"""Обработчик элемента импорта: PDF из MinIO → поля карточки → ответ в шину. Повторы и таймауты — здесь."""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from extraction_worker.src.service.extract import (
    OCR_TIMEOUT,
    PDF_TIMEOUT,
    ItemError,
    LLMUnavailable,
    extract,
    extract_record,
)
from shared.bus.consumer import Delivery
from shared.bus.contracts import ImportTopic, ItemCreatedDTO, ItemExtractedDTO
from shared.bus.publisher import MessageBusPublisher
from shared.config import UPLOAD_LIMITS, llm_config
from shared.exceptions import RetryMessageError
from shared.logging import get_logger
from shared.storage import Storage

logger = get_logger(__name__)

# S3 + pypdf + OCR скана + LLM: своё время на каждую стадию и запас; иначе общий таймаут срабатывает раньше LLM-ного
ITEM_TIMEOUT = PDF_TIMEOUT + OCR_TIMEOUT + llm_config.timeout_seconds + 30
# 20 × 30 с ≈ 10 минут: столько vLLM может грузить модель (healthcheck llm). Задержку не менять:
# TTL уже объявленной очереди повторов RabbitMQ не даст переопределить — воркер не стартует.
MAX_RETRIES = 20
RETRY_DELAY_SECONDS = 30
# PDF и его копия в pypdf — в памяти, параллельно до llm_config.concurrency; больше, чем файл работы, не разбираем
MAX_PDF_BYTES = UPLOAD_LIMITS["workFile"]["maxBytes"]


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
                    if dto.text is not None:  # запись списка текстом: файла нет, сразу ИИ
                        result.fields, result.field_meta = await extract_record(dto.text, dto.work_type, self.http, log)
                    elif dto.file_key:
                        log["stage"] = "s3"  # extract двигает стадию дальше: итоговая строка скажет, где упало
                        if (size := await self.storage.size(dto.file_key)) > MAX_PDF_BYTES:
                            raise ItemError(f"Файл больше {MAX_PDF_BYTES // 1024**2} МБ — ИИ такие не разбирает")
                        pdf = await self.storage.get(dto.file_key)
                        logger.info("PDF получен", extra=log | {"bytes": size})
                        result.fields, result.field_meta = await extract(pdf, self.http, log)
            except LLMUnavailable:
                if delivery.retries < MAX_RETRIES:
                    raise RetryMessageError("LLM-сервер недоступен") from None
                result.error = "ИИ не ответил"
            except TimeoutError:
                logger.warning("Элемент не уложился в таймаут", extra=log | {"timeout": ITEM_TIMEOUT})
                result.error = "ИИ не ответил" if log["stage"] == "ai" else "Файл не удалось разобрать"
            except ItemError as e:
                result.error = str(e)
            except Exception:
                # без ответа элемент навсегда остался бы «в очереди»; ошибку модератор повторит кнопкой
                logger.exception("Элемент не разобран", extra=log)
                result.error = "Файл не удалось разобрать"
        logger.log(
            logging.WARNING if result.error else logging.INFO,
            "Элемент разобран",
            extra=log | {"error": result.error, "duration_ms": round((time.perf_counter() - started) * 1000)},
        )
        try:
            await self.publisher.publish(ImportTopic.ITEM_EXTRACTED, result)
        except Exception:  # publisher уже записал причину в лог
            # dead letters оставили бы элемент «в очереди» навсегда: «Повторить» видит только ошибки
            if delivery.retries < MAX_RETRIES:
                raise RetryMessageError("Результат не опубликован") from None
            raise
