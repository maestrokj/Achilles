"""Change password — tests.html (P1)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog, RefreshToken
from tests.auth.integration.conftest import AuthorizeFn, LoginFn
from tests.factories.users import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CHANGE_URL = "/api/v1/auth/password/change"
NEW_PASSWORD = "brand-new-horse-staple-2027"


async def _change(client: AsyncClient, current: str, new: str):
    return await client.post(CHANGE_URL, json={"current_password": current, "new_password": new})


async def test_change_ok_and_login_with_new(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    assert (await _change(client, DEFAULT_PASSWORD, NEW_PASSWORD)).status_code == 204

    client.headers.pop("Authorization")
    assert (await login(user.email, DEFAULT_PASSWORD)).status_code == 401
    assert (await login(user.email, NEW_PASSWORD)).status_code == 200


async def test_wrong_current_is_generic_401(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await _change(client, "wrong-current-1", NEW_PASSWORD)
    assert resp.status_code == 401
    assert resp.json()["code"] == "INVALID_CREDENTIALS"


async def test_repeated_wrong_current_arms_delay(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    for _ in range(3):
        assert (await _change(client, "wrong-current-1", NEW_PASSWORD)).status_code == 401
    refused = await _change(client, DEFAULT_PASSWORD, NEW_PASSWORD)
    assert refused.status_code == 429


async def test_weak_new_password_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await _change(client, DEFAULT_PASSWORD, "password123")
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "password"


async def test_same_as_current_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await _change(client, DEFAULT_PASSWORD, DEFAULT_PASSWORD)
    assert resp.status_code == 422


async def test_other_sessions_killed_current_kept(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await login(user.email)  # session 1 (older)
    await authorize(user.email)  # session 2 — current, holds the cookie

    assert (await _change(client, DEFAULT_PASSWORD, NEW_PASSWORD)).status_code == 204
    remaining = await db_session.scalar(sa.select(sa.func.count()).select_from(RefreshToken))
    assert remaining == 1, "exactly the current session survives"
    # …and it still refreshes.
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_change_clears_must_change_flag(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    user = await create_user(db_session, must_change_password=True)
    await authorize(user.email)
    assert (await _change(client, DEFAULT_PASSWORD, NEW_PASSWORD)).status_code == 204

    client.headers.pop("Authorization")
    body = (await login(user.email, NEW_PASSWORD)).json()
    assert body["must_change_password"] is False


async def test_audit_written_for_both_outcomes(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    await _change(client, "wrong-current-1", NEW_PASSWORD)
    await _change(client, DEFAULT_PASSWORD, NEW_PASSWORD)

    results = (
        await db_session.scalars(
            sa.select(AuditLog.result).where(AuditLog.action == "password.change")
        )
    ).all()
    assert sorted(results) == ["failure", "success"]
