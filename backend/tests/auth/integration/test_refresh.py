"""Refresh rotation, grace window, family reuse-detection — tests.html (P0)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
import time_machine
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import REFRESH_COOKIE_NAME
from achilles.auth.models import AuditLog, RefreshToken
from tests.auth.integration.conftest import LoginFn
from tests.factories.admin import set_platform_settings
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

REFRESH_URL = "/api/v1/auth/refresh"


def _set_cookie(client: AsyncClient, value: str) -> None:
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, value)


def _current_cookie(client: AsyncClient) -> str:
    values = {c.value for c in client.cookies.jar if c.name == REFRESH_COOKIE_NAME}
    assert len(values) == 1, f"expected one refresh cookie, got {len(values)}"
    value = values.pop()
    assert value is not None
    return value


async def test_refresh_returns_new_pair(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    old_access = (await login(user.email)).json()["access_token"]
    old_refresh = _current_cookie(client)

    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 200
    assert resp.json()["access_token"] != old_access
    assert _current_cookie(client) != old_refresh


async def test_grace_returns_same_new_token(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    """A tab race replays the old cookie within ~10s and must get the same new token."""
    user = await create_user(db_session)
    await login(user.email)
    old_refresh = _current_cookie(client)

    await client.post(REFRESH_URL)
    first_rotation = _current_cookie(client)

    _set_cookie(client, old_refresh)  # the second tab still has the old cookie
    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 200
    match = re.search(rf"{REFRESH_COOKIE_NAME}=([^;]+)", resp.headers["set-cookie"])
    assert match is not None
    assert match.group(1) == first_rotation


async def test_concurrent_refresh_converges_without_error(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    """Two tabs replaying the same cookie at once both succeed and leave at most
    one live token — the FOR UPDATE lock serialises the rotation, the loser lands
    on the grace path. A smoke test of the concurrent path (no deadlock, no dup).
    """
    user = await create_user(db_session)
    await login(user.email)
    original = _current_cookie(client)

    _set_cookie(client, original)
    first, second = await asyncio.gather(client.post(REFRESH_URL), client.post(REFRESH_URL))
    assert first.status_code == 200 and second.status_code == 200

    live = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked.is_(False))
    )
    assert live == 1


async def test_reuse_beyond_grace_kills_family(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, redis_durable: Redis
):
    user = await create_user(db_session)
    await login(user.email)
    old_refresh = _current_cookie(client)

    await client.post(REFRESH_URL)
    rotated = _current_cookie(client)

    # Grace expiry: the redis key has a real 10s TTL — drop it instead of sleeping.
    keys = [k async for k in redis_durable.scan_iter("grace:refresh:*")]
    assert keys
    await redis_durable.delete(*keys)

    _set_cookie(client, old_refresh)
    assert (await client.post(REFRESH_URL)).status_code == 401

    # The whole family is dead: the rotated (newest) token no longer works either.
    _set_cookie(client, rotated)
    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_INVALID"

    # The theft signal leaves an audit trace naming the targeted user — one entry
    # per replay of a revoked token (the old cookie above, then the killed rotated one).
    entries = (
        await db_session.scalars(
            sa.select(AuditLog).where(AuditLog.action == "auth.refresh_reuse_detected")
        )
    ).all()
    assert len(entries) == 2
    assert all(e.result == "failure" and e.actor_id == user.id for e in entries)
    assert all(e.meta is not None and e.meta["family_id"] for e in entries)


async def test_sliding_expiry_30_days(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await login(user.email)
        traveller.shift(timedelta(days=31))
        resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_absolute_ceiling_90_days(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await login(user.email)
        # Keep the session alive by refreshing every 20 days — sliding never expires…
        for _ in range(4):
            traveller.shift(timedelta(days=20))
            assert (await client.post(REFRESH_URL)).status_code == 200
        # …but the family's 90-day absolute ceiling still ends it.
        traveller.shift(timedelta(days=15))
        resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_sliding_window_comes_from_platform_settings(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    """Non-default TTLs, or the assertion would also pass against the constants."""
    await set_platform_settings(
        db_session, refresh_token_ttl=int(timedelta(days=3).total_seconds())
    )
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await login(user.email)
        traveller.shift(timedelta(days=4))  # inside the 30-day default, past the org's 3 days
        resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_absolute_ceiling_comes_from_platform_settings(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    ten_days = int(timedelta(days=10).total_seconds())
    await set_platform_settings(
        db_session, refresh_token_ttl=ten_days, session_absolute_ttl=ten_days
    )
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await login(user.email)
        for _ in range(3):  # refreshing every 3 days keeps the sliding window open…
            traveller.shift(timedelta(days=3))
            assert (await client.post(REFRESH_URL)).status_code == 200
        traveller.shift(timedelta(days=3))  # …day 12, past the org's 10-day ceiling
        resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_shrinking_the_ttl_spares_live_sessions(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    """Lowering a TTL never evicts anyone: the bounds a session lives by were
    written into its token row at sign-in. The new, shorter window only takes
    hold from the next rotation on."""
    user = await create_user(db_session)
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await login(user.email)  # seeded defaults: 30-day sliding, 90-day ceiling
        await set_platform_settings(
            db_session, refresh_token_ttl=int(timedelta(days=1).total_seconds())
        )

        traveller.shift(timedelta(hours=2))
        assert (await client.post(REFRESH_URL)).status_code == 200, "the live session survives"

        traveller.shift(timedelta(days=2))  # the rotated token carries the new 1-day window
        resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_garbage_cookie_rejected(client: AsyncClient):
    _set_cookie(client, "definitely-not-a-token")
    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_INVALID"


async def test_missing_cookie_rejected(client: AsyncClient):
    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
