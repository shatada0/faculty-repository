"""Пароли и выпуск токенов. Единственный сервис, который знает пароли и держит приватный ключ."""

import asyncio
import secrets
from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update

from shared.auth.models import User
from shared.auth.session import SESSION_COLUMNS, SessionUser, issue_token
from shared.exceptions import UnauthorizedError
from shared.logging import get_logger
from shared.uow import UnitOfWorkFactory

logger = get_logger(__name__)
hasher = PasswordHasher()
# Нет такого логина — пароль всё равно сверяется: по времени ответа не понять, есть ли пользователь.
_DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


def _verify(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def user_dict(u: SessionUser) -> dict:
    out = {"id": str(u.id), "name": u.name, "role": u.role}
    if u.person_id:
        out["personId"] = str(u.person_id)
    return out


@dataclass(slots=True)
class AuthService:
    uow_factory: UnitOfWorkFactory

    async def login(self, login: str, password: str, ip: str | None) -> tuple[SessionUser, str]:
        # argon2 — десятки мс и 64 МиБ: сверяем вне транзакции, иначе перебор паролей держит соединения пула.
        async with self.uow_factory() as uow:
            row = (
                await uow.session.execute(select(User.password_hash, *SESSION_COLUMNS).where(User.login == login))
            ).one_or_none()
        password_hash, current = (row[0], SessionUser(*row[1:])) if row else (_DUMMY_HASH, None)
        ok = await asyncio.to_thread(_verify, password_hash, password)
        if not (current and ok and current.is_active):
            logger.warning("Неудачный вход", extra={"login": login, "ip": ip})
            raise UnauthorizedError("Неверный логин или пароль", code="invalid_credentials")
        if hasher.check_needs_rehash(password_hash):
            new_hash = await asyncio.to_thread(hasher.hash, password)
            async with self.uow_factory() as uow:
                await uow.session.execute(
                    update(User)
                    .where(User.id == current.id, User.password_hash == password_hash)
                    .values(password_hash=new_hash)
                )
        logger.info("Вход", extra={"login": login, "ip": ip, "user_id": str(current.id)})
        return current, issue_token(current)

    async def logout(self, user_id: UUID, token_version: int | None) -> None:
        """Гасит все сессии пользователя. Токен со старым ver (сессию уже погасили) ничего не меняет:
        иначе угнанная протухшая кука выбивала бы владельца снова и снова."""
        async with self.uow_factory() as uow:
            await uow.session.execute(
                update(User)
                .where(User.id == user_id, User.token_version == token_version)
                .values(token_version=User.token_version + 1)
            )
        logger.info("Выход", extra={"user_id": str(user_id)})
