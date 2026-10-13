"""Пароли и выпуск токенов. Единственный сервис, который знает пароли и держит приватный ключ."""

import asyncio
import secrets
from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update

from shared.auth import SessionUser, issue_token
from shared.db.uow import UnitOfWorkFactory
from shared.exceptions import UnauthorizedError
from shared.logging import get_logger
from shared.models import User

logger = get_logger(__name__)
hasher = PasswordHasher()
# Нет такого логина — пароль всё равно сверяется: по времени ответа не понять, есть ли пользователь.
_DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


def _verify(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def session_user(u: User) -> SessionUser:
    return SessionUser(u.id, u.name, u.role, u.person_id, u.is_active, u.token_version)


def user_dict(u: SessionUser) -> dict:
    out = {"id": str(u.id), "name": u.name, "role": u.role}
    if u.person_id:
        out["personId"] = str(u.person_id)
    return out


@dataclass(slots=True)
class AuthService:
    uow_factory: UnitOfWorkFactory

    async def login(self, login: str, password: str, ip: str | None) -> tuple[SessionUser, str]:
        async with self.uow_factory() as uow:
            user = (await uow.session.execute(select(User).where(User.login == login))).scalar_one_or_none()
            ok = await asyncio.to_thread(_verify, user.password_hash if user else _DUMMY_HASH, password)
            if not (user and ok and user.is_active):
                logger.warning("Неудачный вход", extra={"login": login, "ip": ip})
                raise UnauthorizedError("Неверный логин или пароль", code="invalid_credentials")
            if hasher.check_needs_rehash(user.password_hash):
                user.password_hash = await asyncio.to_thread(hasher.hash, password)
            current = session_user(user)
        logger.info("Вход", extra={"login": login, "ip": ip, "user_id": str(current.id)})
        return current, issue_token(current)

    async def logout(self, user_id: UUID) -> None:
        """Гасит все сессии пользователя: токены со старым ver больше не проходят."""
        async with self.uow_factory() as uow:
            await uow.session.execute(
                update(User).where(User.id == user_id).values(token_version=User.token_version + 1)
            )
        logger.info("Выход", extra={"user_id": str(user_id)})
