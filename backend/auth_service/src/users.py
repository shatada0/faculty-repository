"""Учётки — командой на сервере, не по HTTP: нет эндпоинта — нечего угонять.

python -m auth_service.src.users add <логин> --name "Модератор" [--role moderator|admin|author] [--person <uuid>]
python -m auth_service.src.users role <логин> <роль> [--person <uuid>]
python -m auth_service.src.users block <логин> [--unblock]
python -m auth_service.src.users password <логин>
Пароль читается из stdin. role, block и password увеличивают token_version — старые сессии гаснут.
"""

import argparse
import asyncio
import getpass
import sys
from uuid import UUID

from sqlalchemy import select

from auth_service.src.api.schemas import PASSWORD_MAX
from auth_service.src.service.auth_service import hasher
from shared.auth.models import User
from shared.logging import get_logger, setup_logging
from shared.uow import uow_write_factory

logger = get_logger("users")
ROLES = ("moderator", "admin", "author")


def read_password() -> str:
    # \r — от файла или echo под Windows, BOM — от конвейера PowerShell ('пароль' | docker compose exec …):
    # иначе они войдут в пароль, и набранным вручную не войти
    piped = not sys.stdin.isatty()
    password = (
        sys.stdin.readline().rstrip("\r\n").lstrip("\N{BYTE ORDER MARK}") if piped else getpass.getpass("Пароль: ")
    )
    if not 8 <= len(password) <= PASSWORD_MAX:
        sys.exit(f"Пароль — от 8 до {PASSWORD_MAX} символов")
    return password


async def run(args: argparse.Namespace) -> None:
    async with uow_write_factory() as uow:
        user = (await uow.session.execute(select(User).where(User.login == args.login))).scalar_one_or_none()
        # без person_id автор войдёт, но любой /api/my ответит 403
        if getattr(args, "role", None) == "author" and not (args.person or (user and user.person_id)):
            sys.exit("Автору нужен --person: id человека из репозитория")
        if args.command == "add":
            if user:
                sys.exit(f"Пользователь {args.login} уже есть")
            # логин сравнивается точно (Shtd и shtd — разные учётки); «shtd » на глаз не отличить и не набрать
            if args.login != args.login.strip():
                sys.exit("Логин с пробелами по краям")
            uow.session.add(
                User(
                    login=args.login,
                    name=args.name,
                    role=args.role,
                    person_id=args.person,
                    password_hash=hasher.hash(read_password()),
                )
            )
        elif user is None:
            sys.exit(f"Нет пользователя {args.login}")
        elif args.command == "role":
            user.role = args.role
            user.person_id = args.person or user.person_id
        elif args.command == "block":
            user.is_active = args.unblock
        elif args.command == "password":
            user.password_hash = hasher.hash(read_password())
        if user is not None:
            user.token_version += 1
    logger.info("Учётка изменена", extra={"command": args.command, "login": args.login})


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m auth_service.src.users")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("login")
    add.add_argument("--name", required=True)
    add.add_argument("--role", choices=ROLES, default="moderator")
    add.add_argument("--person", type=UUID)
    role = sub.add_parser("role")
    role.add_argument("login")
    role.add_argument("role", choices=ROLES)
    role.add_argument("--person", type=UUID)
    block = sub.add_parser("block")
    block.add_argument("login")
    block.add_argument("--unblock", action="store_true")
    sub.add_parser("password").add_argument("login")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
