"""MinIO через aioboto3: put, get, presign. Общего диска у сервисов нет — все файлы здесь."""

import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import IO, Any
from urllib.parse import quote

import aioboto3
from botocore.config import Config
from fastapi import FastAPI

from shared.config import s3_config
from shared.logging import get_logger, log_loop_errors

logger = get_logger(__name__)

LINK_TTL = timedelta(hours=1)
CHUNK = 1024 * 1024
_S3 = Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3})


def content_disposition(kind: str, filename: str) -> str:
    """Только filename*=UTF-8'' через quote(): перевод строки в заголовок не пройдёт."""
    return f"{kind}; filename*=UTF-8''{quote(filename, safe='')}"


class Storage:
    """Два клиента: рабочий ходит в http://minio:9000, второй только подписывает ссылки для files.<домен>."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.bucket = s3_config.bucket

    async def start(self) -> None:
        session = aioboto3.Session(
            aws_access_key_id=s3_config.access_key,
            aws_secret_access_key=s3_config.secret_key,
            region_name=s3_config.region,
        )
        self._client = await self._stack.enter_async_context(
            session.client("s3", endpoint_url=s3_config.endpoint, config=_S3)
        )
        # Подпись SigV4 включает хост: presign считается для того адреса, который видит браузер. Этот клиент никуда не ходит.
        self._signer = await self._stack.enter_async_context(
            session.client("s3", endpoint_url=s3_config.public_url, config=_S3)
        )

    async def close(self) -> None:
        await self._stack.aclose()

    async def put(self, key: str, body: bytes | IO[bytes], content_type: str) -> None:
        started = time.perf_counter()
        if isinstance(body, bytes):
            await self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        else:
            # upload_fileobj сам режет большие файлы на multipart
            await self._client.upload_fileobj(body, self.bucket, key, ExtraArgs={"ContentType": content_type})
        logger.info("S3 put", extra={"key": key, "duration_ms": round((time.perf_counter() - started) * 1000)})

    async def get(self, key: str) -> bytes:
        obj = await self._client.get_object(Bucket=self.bucket, Key=key)
        async with obj["Body"] as stream:
            return await stream.read()

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        """Объект кусками по 1 МБ: zip-выгрузка не держит файл в памяти целиком."""
        body = (await self._client.get_object(Bucket=self.bucket, Key=key))["Body"]
        # as-цель не брать: __aenter__ отдаёт сырой aiohttp-ответ, у его read() нет размера куска
        async with body:
            async for chunk in body.iter_chunks(CHUNK):
                yield chunk

    async def copy(self, source: str, key: str) -> None:
        await self._client.copy_object(Bucket=self.bucket, Key=key, CopySource={"Bucket": self.bucket, "Key": source})

    async def delete(self, key: str) -> None:
        await self._client.delete_object(Bucket=self.bucket, Key=key)

    async def presign(self, key: str, filename: str, *, inline: bool) -> dict[str, Any]:
        """FileLink {url, expiresAt}. PDF — inline (iframe редактора), остальное — attachment с исходным именем."""
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentDisposition": content_disposition("inline" if inline else "attachment", filename),
        }
        url = await self._signer.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=int(LINK_TTL.total_seconds())
        )
        return {"url": url, "expiresAt": (datetime.now(UTC) + LINK_TTL).isoformat()}


def photo_url(key: str) -> str:
    """Постоянная ссылка на фото: people/* открыт на анонимное чтение, кэш браузера работает."""
    return f"{s3_config.public_url}/{s3_config.bucket}/{key}"


def detect_type(head: bytes) -> str | None:
    """Тип файла по сигнатуре, а не по имени: pdf или zip-контейнер (docx, pptx, zip)."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    return None


@asynccontextmanager
async def storage_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Клиент MinIO на всё время жизни сервиса: app.state.storage."""
    log_loop_errors()
    app.state.storage = Storage()
    try:
        await app.state.storage.start()
    except Exception:
        logger.exception("Сервис не стартовал: нет MinIO")
        raise
    yield
    await app.state.storage.close()
