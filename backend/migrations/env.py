"""alembic с asyncpg (шаблон async): psycopg — LGPL, его не берём. URL — из database_config (владелец схемы)."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import shared.models  # noqa: F401 — все модели в metadata
from shared.config import database_config
from shared.db.base import Base

target_metadata = Base.metadata


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(database_config.url, hide_parameters=True)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(main())
