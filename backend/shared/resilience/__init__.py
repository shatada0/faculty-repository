"""Защита внешних API: ограничитель частоты и автомат (circuit breaker), по одному охраннику на сервис.

Ограничитель выпускает запросы к сервису не чаще rate в секунду: вежливо к Crossref, ORCID, OpenAlex и Open Library
и без 429 на пачках подряд. Автомат после FAILURES неудач подряд (сеть, таймаут, 5xx, 429) размыкается на reset
секунд (+0–20 % джиттера): запросы сразу получают CircuitOpenError, а не ждут таймаута лежащего сервиса.
Затем пропускается одна проба: удалась — цепь замкнута, нет — снова разомкнута. 4xx — ошибка запроса, а не сервиса: не считается.

Все вызовы идут через guard(name).request(...): один охранник на сервис в процессе, поэтому кнопка в рабочем
месте и ночная команда в одном процессе делят лимит.
ponytail: состояние в памяти процесса — у каждой реплики и каждой ночной команды своё; общий лимит на несколько
реплик — через Redis, когда реплик станет больше одной.
"""

import asyncio
import random
import time

import httpx

from shared.exceptions import UpstreamError
from shared.logging import get_logger

logger = get_logger(__name__)

FAILURES = 5
# сервис → (запросов в секунду, на сколько секунд размыкается). С запасом от лимитов сервисов.
SERVICES: dict[str, tuple[float | None, float]] = {
    "Crossref": (3.0, 60),
    "ORCID": (8.0, 60),
    "OpenAlex": (5.0, 60),
    "Open Library": (1.0, 60),  # Open Library просит не нагружать: раз в секунду
    "LLM": (None, 30),  # частоту держит prefetch очереди; 30 с — как задержка повтора сообщения
}


class CircuitOpenError(UpstreamError):
    """Сервис только что падал несколько раз подряд — не ждём его таймаута."""


class Guard:
    def __init__(self, name: str, per_second: float | None, reset_seconds: float) -> None:
        self.name = name
        self.reset_seconds = reset_seconds
        self._interval = 1 / per_second if per_second else 0.0
        self._next = 0.0  # когда можно выпустить следующий запрос
        self._lock = asyncio.Lock()
        self._failures = 0
        self._opened_at: float | None = None  # None — цепь замкнута
        self._open_for = reset_seconds  # пауза этого размыкания: reset_seconds с джиттером
        self._probe = False  # пробный запрос после паузы уже идёт

    async def request(self, http: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
        self._enter()
        await self._wait()
        try:
            response = await http.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.TransportError:  # не подключились, оборвалось, таймаут
            self._failed()
            raise
        except BaseException:  # отмена снаружи: проба не состоялась, следующая попробует снова
            self._probe = False
            raise
        if response.status_code >= 500 or response.status_code == 429:
            self._failed()
        else:
            self._succeeded()
        return response

    def _enter(self) -> None:
        if self._opened_at is None:
            return
        if self._probe or time.monotonic() - self._opened_at < self._open_for:
            raise CircuitOpenError(f"{self.name} временно недоступен, попробуйте через минуту")
        self._probe = True  # пауза вышла: пропускаем одну пробу

    async def _wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)

    def _failed(self) -> None:
        self._probe = False
        self._failures += 1
        if self._opened_at is None and self._failures < FAILURES:
            return
        if self._opened_at is None:
            logger.warning("Цепь разомкнута", extra={"upstream": self.name, "seconds": self.reset_seconds})
        self._opened_at = time.monotonic()  # неудачная проба — ещё одна пауза
        # +0–20 %: реплики, разомкнувшиеся вместе, не шлют пробу в одну секунду
        self._open_for = self.reset_seconds * random.uniform(1, 1.2)  # noqa: S311 — джиттер, не криптография

    def _succeeded(self) -> None:
        if self._opened_at is not None:
            logger.info("Цепь замкнута", extra={"upstream": self.name})
        self._failures, self._opened_at, self._probe = 0, None, False


_guards: dict[str, Guard] = {}


def guard(name: str) -> Guard:
    if name not in _guards:
        per_second, reset = SERVICES[name]
        _guards[name] = Guard(name, per_second, reset)
    return _guards[name]


def reset_all() -> None:
    """Для тестов: каждый начинает с замкнутыми цепями."""
    _guards.clear()
