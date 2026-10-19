"""auth_service и мидлвари: токены, проверка Origin, единый формат ошибок."""

from datetime import UTC, datetime, timedelta

import httpx
import jwt

from admin_service.src.main import app as admin_app
from auth_service.src.main import app as auth_app
from shared.auth.session import SessionUser, _key, issue_token, read_token

USER = SessionUser(
    id=__import__("uuid").uuid4(), name="Модератор", role="moderator", person_id=None, is_active=True, token_version=0
)


async def _post(app, path: str, headers: dict, json: object = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.post(path, headers=headers, json=json)


def test_tokens_eddsa_only() -> None:
    assert read_token(issue_token(USER))["sub"] == str(USER.id)
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
