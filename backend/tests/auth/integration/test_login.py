"""Login: the single password entry — tests.html (P0)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import User
from achilles.auth.security.jwt import decode_access_token
from achilles.config import Settings
from tests.auth.integration.conftest import LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def test_login_success_shape(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    user = await create_user(db_session)
    resp = await login(user.email)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == user.email
    assert "__Secure-refresh=" in resp.headers["set-cookie"]


async def test_refresh_cookie_flags(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    user = await create_user(db_session)
    set_cookie = (await login(user.email)).headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "Path=/api/v1/auth" in set_cookie


async def test_remember_me_controls_cookie_lifetime(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    session_cookie = (await login(user.email)).headers["set-cookie"]
    assert "Max-Age" not in session_cookie

    persistent_cookie = (await login(user.email, remember_me=True)).headers["set-cookie"]
    assert "Max-Age=2592000" in persistent_cookie  # 30 days


@pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "deactivated"])
async def test_all_failures_look_identical(
    case: str, client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(
        db_session, status="deactivated" if case == "deactivated" else "active"
    )
    email = "ghost@example.com" if case == "unknown_email" else user.email
    password = "wrong-password-123" if case == "wrong_password" else None

    resp = await login(email, password) if password else await login(email)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "INVALID_CREDENTIALS"
    # Nothing distinguishes the cases beyond the per-request id.
    assert body["detail"] == "Invalid credentials"


async def test_email_is_case_insensitive(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session, email="mixed.case@example.com")
    resp = await login("Mixed.Case@Example.Com")
    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == user.id


async def test_access_token_claims(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, test_settings: Settings
):
    user = await create_user(db_session, role="admin")
    token = (await login(user.email)).json()["access_token"]
    claims = decode_access_token(token, secret=test_settings.secret_key)
    assert claims.user_id == user.id
    assert claims.role == "admin"


async def test_last_login_updated(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    user = await create_user(db_session)
    assert user.last_login_at is None
    await login(user.email)
    fresh = await db_session.get(User, user.id, populate_existing=True)
    assert fresh is not None
    assert fresh.last_login_at is not None
