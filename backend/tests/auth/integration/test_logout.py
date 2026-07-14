"""Logout: single session and all devices — tests.html (P0)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import REFRESH_COOKIE_NAME
from achilles.auth.models import RefreshToken
from tests.auth.integration.conftest import LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

LOGOUT_URL = "/api/v1/auth/logout"
LOGOUT_ALL_URL = "/api/v1/auth/logout-all"
REFRESH_URL = "/api/v1/auth/refresh"


async def test_logout_deletes_session_and_clears_cookie(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)

    resp = await client.post(LOGOUT_URL)
    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"]
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires" in set_cookie.lower()
    assert resp.headers["Clear-Site-Data"]

    count = await db_session.scalar(sa.select(sa.func.count()).select_from(RefreshToken))
    assert count == 0


async def test_refresh_after_logout_rejected(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)
    stolen = {c.value for c in client.cookies.jar if c.name == REFRESH_COOKIE_NAME}.pop()
    assert stolen is not None

    await client.post(LOGOUT_URL)
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, stolen)
    assert (await client.post(REFRESH_URL)).status_code == 401


async def test_logout_without_cookie_is_401(client: AsyncClient):
    assert (await client.post(LOGOUT_URL)).status_code == 401


async def test_logout_all_kills_every_session(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)  # session 1
    body = (await login(user.email)).json()  # session 2 (this client keeps its cookie)

    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    assert (await client.post(LOGOUT_ALL_URL)).status_code == 204

    count = await db_session.scalar(sa.select(sa.func.count()).select_from(RefreshToken))
    assert count == 0
    assert (await client.post(REFRESH_URL)).status_code == 401


async def test_access_token_survives_logout_all(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    """Stateless access lives out its ≤15-min window — by design."""
    user = await create_user(db_session)
    body = (await login(user.email)).json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"

    assert (await client.post(LOGOUT_ALL_URL)).status_code == 204
    # The same access token still authenticates a second call.
    assert (await client.post(LOGOUT_ALL_URL)).status_code == 204
