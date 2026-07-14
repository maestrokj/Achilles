"""Temp-password gate: the boundary is server-side — tests.html (P0)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

LOGOUT_ALL_URL = "/api/v1/auth/logout-all"
LOGOUT_URL = "/api/v1/auth/logout"


async def test_login_issues_tokens_but_flags_gate(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session, must_change_password=True)
    body = (await login(user.email)).json()
    assert body["access_token"], "tokens are issued — the gate is not a login ban"
    assert body["must_change_password"] is True


async def test_gate_blocks_other_requests(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session, must_change_password=True)
    body = (await login(user.email)).json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"

    resp = await client.post(LOGOUT_ALL_URL)
    assert resp.status_code == 403
    assert resp.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


async def test_gate_allows_logout(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    user = await create_user(db_session, must_change_password=True)
    await login(user.email)
    assert (await client.post(LOGOUT_URL)).status_code == 204


async def test_relogin_without_change_still_gated(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session, must_change_password=True)
    await login(user.email)
    await client.post(LOGOUT_URL)

    body = (await login(user.email)).json()
    assert body["must_change_password"] is True
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    assert (await client.post(LOGOUT_ALL_URL)).status_code == 403
