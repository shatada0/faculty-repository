"""Маленькие настоящие реализации вместо Mock: тест проверяет результат, а не порядок вызовов."""

from collections.abc import AsyncIterator
from typing import IO

from pydantic import BaseModel


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes | IO[bytes], content_type: str) -> None:
        self.objects[key] = body if isinstance(body, bytes) else body.read()

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        yield self.objects.get(key, b"%PDF-1.4 demo")

    async def copy(self, source: str, key: str) -> None:
        self.objects[key] = self.objects[source]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def presign(self, key: str, filename: str, *, inline: bool) -> dict:
        return {"url": f"https://files.test/{key}?inline={inline}", "expiresAt": "2030-01-01T00:00:00+00:00"}


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel]] = []

    async def publish(self, topic: str, dto: BaseModel) -> None:
        self.published.append((topic, dto))
