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

    async def size(self, key: str) -> int:
        return len(self.objects[key])

    async def stream(self, key: str) -> AsyncIterator[bytes]:
        yield self.objects.get(key, b"%PDF-1.4 demo")

    async def copy(self, source: str, key: str) -> None:
        self.objects[key] = self.objects[source]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def presign(self, key: str, filename: str, *, inline: bool) -> dict:
        return {"url": f"https://files.test/{key}?inline={inline}", "expiresAt": "2030-01-01T00:00:00+00:00"}


class FakePublisher:
    """Из outbox событие приходит словарём (payload), от воркера — моделью."""

    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel | dict]] = []

    async def publish(self, topic: str, dto: BaseModel | dict, **_: str | None) -> None:
        self.published.append((topic, dto))


def deps(d):
    """Все зависимости ручки вглубь: require_* может висеть на роутере, а не на самой функции."""
    for s in d.dependencies:
        yield s.call
        yield from deps(s)


async def anonymous_everywhere(app, routers) -> None:
    """Каждая ручка каждого роутера без куки отвечает 401 — до разбора тела и параметров."""
    import re

    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        for router in routers:
            for r in router.routes:
                path = re.sub(r"\{[^}]+\}", "0190a0a0-0000-7000-8000-000000000000", r.path).replace(
                    "{work_type}", "vkr"
                )
                for method in r.methods:
                    response = await client.request(method, path)
                    assert response.status_code == 401, (method, r.path)
