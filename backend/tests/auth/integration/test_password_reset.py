"""Forgot / reset password — tests.html (P1).

The forgot flow is queue-first (stage 9): the route only rate-limits and
enqueues; lookup + token + letter happen in the worker (`outbox.drain()`).
"""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog, RefreshToken, ResetToken
from tests.auth.integration.conftest import LoginFn, Outbox, set_smtp
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

FORGOT_URL = "/api/v1/auth/password/forgot"
RESET_URL = "/api/v1/auth/password/reset"
NEW_PASSWORD = "brand-new-horse-staple-2027"


async def _forgot(client: AsyncClient, email: str):
    return await client.post(FORGOT_URL, json={"email": email})


async def test_known_and_unknown_email_answer_identically(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
):
    user = await create_user(db_session)
    known = await _forgot(client, user.email)
    unknown = await _forgot(client, "ghost@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(outbox.jobs) == 2, "both answers enqueue the same-shaped job"

    letters = await outbox.drain()
    assert [letter.to for letter in letters] == [user.email], "only the real account got a letter"


async def test_resend_rate_limited(client: AsyncClient, db_session: AsyncSession, outbox: Outbox):
    del outbox
    user = await create_user(db_session)
    for _ in range(3):
        assert (await _forgot(client, user.email)).status_code == 200
    refused = await _forgot(client, user.email)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]


async def test_new_link_kills_previous(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
):
    user = await create_user(db_session)
    await _forgot(client, user.email)
    await _forgot(client, user.email)
    letters = await outbox.drain()
    first_token, second_token = letters[0].token, letters[1].token

    resp = await client.post(RESET_URL, json={"token": first_token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 410
    assert resp.json()["code"] == "RESET_EXPIRED"
    assert (
        await client.post(RESET_URL, json={"token": second_token, "new_password": NEW_PASSWORD})
    ).status_code == 204


async def test_sending_link_does_not_kill_sessions(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, outbox: Outbox
):
    user = await create_user(db_session)
    await login(user.email)
    await _forgot(client, user.email)
    await outbox.drain()
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_reset_sets_password_kills_sessions_and_audits(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, outbox: Outbox
):
    user = await create_user(db_session)
    await login(user.email)
    await _forgot(client, user.email)
    (letter,) = await outbox.drain()

    resp = await client.post(RESET_URL, json={"token": letter.token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 204

    assert await db_session.scalar(sa.select(sa.func.count()).select_from(RefreshToken)) == 0
    assert (await login(user.email, NEW_PASSWORD)).status_code == 200
    audit_entry = await db_session.scalar(
        sa.select(AuditLog).where(AuditLog.action == "password.reset")
    )
    assert audit_entry is not None


async def test_forgot_request_is_audited_by_the_worker(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
):
    user = await create_user(db_session)
    await _forgot(client, user.email)
    await outbox.drain()
    audit_entry = await db_session.scalar(
        sa.select(AuditLog).where(AuditLog.action == "password.reset_request")
    )
    assert audit_entry is not None
    assert audit_entry.target_id == str(user.id)


async def test_token_reuse_is_410(client: AsyncClient, db_session: AsyncSession, outbox: Outbox):
    user = await create_user(db_session)
    await _forgot(client, user.email)
    (letter,) = await outbox.drain()
    token = letter.token
    assert (
        await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    ).status_code == 204
    reuse = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    assert reuse.status_code == 410
    assert reuse.json()["code"] == "RESET_EXPIRED"


async def test_garbage_token_is_410(client: AsyncClient):
    resp = await client.post(RESET_URL, json={"token": "nope", "new_password": NEW_PASSWORD})
    assert resp.status_code == 410


async def test_unconfigured_smtp_sends_nothing(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
):
    """SMTP switched off between enqueue and pickup: the job stays silent."""
    await set_smtp(db_session, enabled=False)
    user = await create_user(db_session)
    assert (await _forgot(client, user.email)).status_code == 200
    letters = await outbox.drain()
    assert letters == []
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(ResetToken)) == 0
