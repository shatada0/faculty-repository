"""Учётки — командой на сервере, не по HTTP: нет эндпоинта — нечего угонять.

python -m auth_service.users add <логин> --name "Модератор" [--role moderator|admin|author] [--person <uuid>]
python -m auth_service.users role <логин> <роль>
python -m auth_service.users block <логин> [--unblock]
python -m auth_service.users password <логин>
Пароль читается из stdin. role, block и password увеличивают token_version — старые сессии гаснут.
"""

import argparse
import asyncio
import getpass
import sys
from uuid import UUID

from sqlalchemy import select

from auth_service.service import hasher
from shared.db.uow import uow_write_factory
from shared.logging import get_logger, setup_logging
from shared.models import User

logger = get_logger("users")
ROLES = ("moderator", "admin", "author")


def read_password() -> str:
    password = getpass.getpass("Пароль: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    if len(password) < 8:
        sys.exit("Пароль короче 8 символов")
    return password


async def run(args: argparse.Namespace) -> None:
    async with uow_write_factory() as uow:
        user = (await uow.session.execute(select(User).where(User.login == args.login))).scalar_one_or_none()
        if args.command == "add":
            if user:
                sys.exit(f"Пользователь {args.login} уже есть")
            if args.role == "author" and not args.person:
                sys.exit("Автору нужен --person: id человека из репозитория")
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
        elif args.command == "block":
            user.is_active = args.unblock
        elif args.command == "password":
            user.password_hash = hasher.hash(read_password())
        if user is not None:
            user.token_version += 1
    logger.info("Учётка изменена", extra={"command": args.command, "login": args.login})


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m auth_service.users")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("login")
    add.add_argument("--name", required=True)
    add.add_argument("--role", choices=ROLES, default="moderator")
    add.add_argument("--person", type=UUID)
    role = sub.add_parser("role")
    role.add_argument("login")
    role.add_argument("role", choices=ROLES)
    block = sub.add_parser("block")
    block.add_argument("login")
    block.add_argument("--unblock", action="store_true")
    sub.add_parser("password").add_argument("login")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
