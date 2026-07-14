"""Three-layer brute-force barrier — tests.html (P1)."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

WRONG = "definitely-wrong-password"
T0 = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


async def test_ip_window_blocks_21st_attempt(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    # Distinct emails: only the shared IP counter is in play.
    for i in range(20):
        resp = await login(f"ghost{i}@example.com", WRONG)
        assert resp.status_code == 401

    refused = await login(user.email)  # even the correct password is refused now
    assert refused.status_code == 429
    body = refused.json()
    assert body["code"] == "RATE_LIMITED"
    assert refused.headers["Retry-After"] == str(body["retry_after"])


async def test_xff_header_cannot_dodge_the_ip_window(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    await create_user(db_session)
    for i in range(20):
        await client.post(
            "/api/v1/auth/login",
            json={"email": f"ghost{i}@example.com", "password": WRONG},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost99@example.com", "password": WRONG},
        headers={"X-Forwarded-For": "10.9.9.9"},
    )
    assert resp.status_code == 429, "spoofed XFF must not reset the window"


async def test_first_two_failures_are_free(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    for _ in range(2):
        assert (await login(user.email, WRONG)).status_code == 401
    assert (await login(user.email)).status_code == 200


async def test_third_failure_arms_exponential_delay(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    with time_machine.travel(T0, tick=False):
        for _ in range(3):
            assert (await login(user.email, WRONG)).status_code == 401
        refused = await login(user.email)
        assert refused.status_code == 429
        assert refused.json()["retry_after"] == 1


async def test_delay_grows_and_caps_at_30s(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    with time_machine.travel(T0, tick=False) as traveller:
        for _ in range(8):  # failures 3..8 arm 1,2,4,8,16,30 — travel past each delay
            assert (await login(user.email, WRONG)).status_code == 401
            traveller.shift(timedelta(seconds=31))
        assert (await login(user.email, WRONG)).status_code == 401  # 9th failure
        refused = await login(user.email)
        assert refused.status_code == 429
        assert refused.json()["retry_after"] == 30


async def test_success_resets_the_counter(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    for _ in range(2):
        await login(user.email, WRONG)
    assert (await login(user.email)).status_code == 200
    # Counter went back to zero: two more failures are free again.
    for _ in range(2):
        assert (await login(user.email, WRONG)).status_code == 401
    assert (await login(user.email)).status_code == 200


async def test_email_case_shares_the_counter(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session, email="cased@example.com")
    with time_machine.travel(T0, tick=False):
        assert (await login("cased@example.com", WRONG)).status_code == 401
        assert (await login("Cased@Example.Com", WRONG)).status_code == 401
        assert (await login("CASED@EXAMPLE.COM", WRONG)).status_code == 401
        assert (await login(user.email)).status_code == 429


async def test_alert_fires_at_ten_failures(
    client: AsyncClient,
    db_session: AsyncSession,
    login: LoginFn,
    caplog: pytest.LogCaptureFixture,
):
    user = await create_user(db_session)
    with (
        time_machine.travel(T0, tick=False) as traveller,
        caplog.at_level(logging.WARNING, logger="achilles.auth.services.brute_force"),
    ):
        for _ in range(10):
            assert (await login(user.email, WRONG)).status_code == 401
            traveller.shift(timedelta(seconds=31))
    assert any("Brute-force alert" in r.message for r in caplog.records)
