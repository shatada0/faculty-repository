"""auth_service и мидлвари: токены, проверка Origin, единый формат ошибок."""

import io
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from admin_service.src.main import app as admin_app
from auth_service.src.main import app as auth_app
from auth_service.src.service.auth_service import hasher
from auth_service.src.users import read_password
from shared.auth.models import User
from shared.auth.session import SessionUser, _key, issue_token, read_token
from shared.config import AuthConfig
from shared.uow import uow_ro_factory, uow_write_factory

USER = SessionUser(
    id=__import__("uuid").uuid4(), name="Модератор", role="moderator", person_id=None, is_active=True, token_version=0
)


async def _post(app, path: str, headers: dict, json: object = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.post(path, headers=headers, json=json)


def test_tokens_eddsa_only() -> None:
    claims = read_token(issue_token(USER))
    assert claims["sub"] == str(USER.id) and "name" not in claims  # имя — из базы через /me, не из куки
    now = datetime.now(UTC)
    claims = {"sub": str(USER.id), "ver": 0, "exp": now + timedelta(hours=1)}
    assert read_token(jwt.encode(claims, key=None, algorithm="none")) is None
    assert read_token(jwt.encode(claims, "s" * 32, algorithm="HS256")) is None
    assert (
        read_token(jwt.encode(claims | {"exp": now - timedelta(seconds=1)}, _key("jwt_private"), algorithm="EdDSA"))
        is None
    )
    assert read_token(issue_token(USER)[:-4] + "AAAA") is None


async def test_origin_check_blocks_foreign_sites() -> None:
    r = await _post(auth_app, "/api/auth/logout", {"origin": "https://student.example.ru"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    r = await _post(auth_app, "/api/auth/logout", {"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert (await _post(auth_app, "/api/auth/logout", {"origin": "https://localhost"})).status_code == 204
    assert (await _post(auth_app, "/api/auth/logout", {})).status_code == 204  # не браузер: curl, тесты


async def test_errors_have_one_format_and_trace_id() -> None:
    r = await _post(auth_app, "/api/auth/login", {"x-request-id": "abc"}, {"login": "x"})
    assert r.status_code == 422 and r.headers["x-request-id"] == "abc"
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["fields"] == [{"field": "password", "message": "Field required"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://t") as client:
        r = await client.get("/api/admin/summary")
    assert r.status_code == 401 and r.json() == {"error": {"code": "unauthorized", "message": "Нужен вход"}}


async def test_login_me_logout(demo: dict) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)  # старые параметры: при входе хеш пересчитается
    users = {
        "ivanova": User(login="ivanova", name="Иванова", role="moderator", password_hash=weak.hash("пароль-123")),
        "blocked": User(login="blocked", name="X", role="moderator", password_hash=hasher.hash("пароль-123")),
    }
    users["blocked"].is_active = False
    async with uow_write_factory() as uow:
        uow.session.add_all(users.values())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://t") as client:

        async def call(method: str, path: str, cookie: str | None = None, json: object = None) -> httpx.Response:
            client.cookies.clear()  # кука — только явная: проверяем и старую, и её отсутствие
            headers = {"origin": "https://localhost"} | ({"cookie": f"session_token={cookie}"} if cookie else {})
            return await client.request(method, path, headers=headers, json=json)

        for login, password in (("ivanova", "не тот"), ("nobody", "пароль-123"), ("blocked", "пароль-123")):
            r = await call("POST", "/api/auth/login", json={"login": login, "password": password})
            assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"

        r = await call("POST", "/api/auth/login", json={"login": "ivanova", "password": "пароль-123"})
        assert r.status_code == 200 and r.json()["user"]["role"] == "moderator"
        set_cookie = r.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "samesite=lax" in set_cookie
        assert r.headers["cache-control"] == "no-store"
        token = r.cookies["session_token"]

        r = await call("GET", "/api/auth/me", token)
        assert r.json()["user"]["name"] == "Иванова" and r.headers["cache-control"] == "no-store"
        assert (await call("GET", "/api/auth/me")).status_code == 401
        assert (await call("POST", "/api/auth/logout", token)).status_code == 204
        assert (await call("GET", "/api/auth/me", token)).status_code == 401

        # новая сессия переживает выход со старой, уже погашенной куки
        r = await call("POST", "/api/auth/login", json={"login": "ivanova", "password": "пароль-123"})
        fresh = r.cookies["session_token"]
        assert (await call("POST", "/api/auth/logout", token)).status_code == 204
        assert (await call("GET", "/api/auth/me", fresh)).status_code == 200

    async with uow_ro_factory() as uow:
        stored = (await uow.session.execute(select(User).where(User.login == "ivanova"))).scalar_one()
    assert stored.token_version == 1 and not hasher.check_needs_rehash(stored.password_hash)


def test_host_cookie_requires_secure() -> None:
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE"):
        AuthConfig(cookie_name="__Host-session", cookie_secure=False)


def test_password_from_powershell_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """'moderator123' | docker compose exec … в PowerShell приходит с BOM и \r\n: в пароль они не попадают."""
    monkeypatch.setattr("sys.stdin", io.StringIO("\N{BYTE ORDER MARK}moderator123\r\n"))
    assert read_password() == "moderator123"
