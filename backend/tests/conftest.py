"""Окружение тестов: ключи Ed25519 во временной папке, dev-кука, фейковое хранилище."""

import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_keys = Path(tempfile.mkdtemp())
_private = Ed25519PrivateKey.generate()
(_keys / "jwt_private").write_bytes(
    _private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
)
(_keys / "jwt_public").write_bytes(
    _private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
)
# до импорта shared.config: настройки читаются один раз при импорте
os.environ |= {
    "AUTH_KEYS_DIR": str(_keys),
    "AUTH_COOKIE_NAME": "session_token",
    "AUTH_COOKIE_SECURE": "false",
    "AUTH_ALLOWED_ORIGINS": '["https://localhost"]',
    "DB_HOST": "localhost",
}


import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import create_async_engine

import shared.uow as uow_module
from admin_service.src import refdata, seed
from shared.auth.models import User
from shared.auth.session import SessionUser
from shared.resilience import reset_all
from shared.uow import uow_write_factory

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def closed_circuits() -> None:
    """Автоматы внешних сервисов — в памяти процесса: тест с падающим сервисом не должен разомкнуть их следующему."""
    reset_all()


@pytest.fixture(scope="session")
async def demo() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("нужен Docker для testcontainers")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17", username="app", password="app", dbname="app", driver=None) as pg:
        host, port = pg.get_container_host_ip(), int(pg.get_exposed_port(5432))
        env = os.environ | {"DB_HOST": host, "DB_PORT": str(port), "DB_USER": "app", "DB_PASSWORD": "app"}
        alembic = [sys.executable, "-m", "alembic", "upgrade", "head"]
        await asyncio.to_thread(subprocess.run, alembic, cwd=ROOT, env=env, check=True)
        engine = create_async_engine(URL.create("postgresql+asyncpg", "app", "app", host, port, "app"))
        uow_module.sessions.configure(bind=engine)
        await refdata.init()
        data, ids = await seed.write_demo()
        moderator = User(id=uuid.uuid7(), login="moderator", password_hash="x", role="moderator", name="Модератор")
        async with uow_write_factory() as uow:
            uow.session.add(moderator)
        yield {
            "data": data,
            "ids": ids,
            "user": SessionUser(moderator.id, "Модератор", "moderator", None, True, 0),
            "url": URL.create("postgresql+asyncpg", "app", "app", host, port, "app"),
        }
        await engine.dispose()
