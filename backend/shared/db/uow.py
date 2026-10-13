"""Unit of Work: одна операция сервиса — одна транзакция. Чтение всегда заканчивается откатом."""

from collections.abc import Callable
from functools import partial
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.config import database_config


class UnitOfWork:
    """Всё внутри async with фиксируется целиком или откатывается."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, read_only: bool = False) -> None:
        self._sessions = sessions
        self._read_only = read_only

    async def __aenter__(self) -> Self:
        self.session = self._sessions()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        try:
            if exc_type is not None:
                await self.session.rollback()
            elif not self._read_only:
                await self.session.commit()
            # Чтение не откатываем явно: rollback() помечает прочитанные объекты устаревшими, и после выхода из with
            # обращение к их полям падает. Транзакцию откатит пул, когда close() вернёт ему соединение.
        finally:
            await self.session.close()


UnitOfWorkFactory = Callable[[], UnitOfWork]

# hide_parameters: в параметрах запросов — персональные данные, в лог они не попадут.
engine = create_async_engine(database_config.url, hide_parameters=True, pool_pre_ping=True)
# expire_on_commit=False: объекты читаются и после коммита, без ленивой подгрузки (в async она падает)
sessions = async_sessionmaker(engine, expire_on_commit=False)

uow_write_factory: UnitOfWorkFactory = partial(UnitOfWork, sessions)
uow_ro_factory: UnitOfWorkFactory = partial(UnitOfWork, sessions, read_only=True)
