"""Setup Wizard: exists only at 0 users — tests.html (P0)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.models import User
from achilles.auth.services.bootstrap import create_owner
from tests.factories.users import DEFAULT_PASSWORD as STRONG_PASSWORD
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

SETUP_URL = "/api/v1/auth/setup"


def _payload(**overrides: str) -> dict[str, str]:
    return {
        "email": "owner@example.com",
        "full_name": "First Owner",
        "password": STRONG_PASSWORD,
    } | overrides


async def test_setup_creates_owner_at_zero_users(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(SETUP_URL, json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["role"] == "owner"
    assert "__Secure-refresh=" in resp.headers.get("set-cookie", "")

    user = await db_session.scalar(sa.select(User))
    assert user is not None
    assert user.email == "owner@example.com"
    assert user.role == "owner"


async def test_setup_probe_true_at_zero_users(client: AsyncClient):
    resp = await client.get(SETUP_URL)
    assert resp.status_code == 200
    assert resp.json() == {"needs_setup": True}


async def test_setup_probe_false_after_owner(client: AsyncClient, db_session: AsyncSession):
    await create_user(db_session)
    resp = await client.get(SETUP_URL)
    assert resp.status_code == 200
    assert resp.json() == {"needs_setup": False}


async def test_setup_gone_after_first_user(client: AsyncClient, db_session: AsyncSession):
    await create_user(db_session)
    resp = await client.post(SETUP_URL, json=_payload())
    assert resp.status_code == 404
    assert resp.json()["code"] == "SETUP_UNAVAILABLE"


async def test_setup_race_loser_gets_conflict(client: AsyncClient, db_session: AsyncSession):
    """The advisory-locked service path itself answers 409 when the race is lost."""
    assert (await client.post(SETUP_URL, json=_payload())).status_code == 201
    with pytest.raises(ApiError) as exc_info:
        await create_owner(
            db_session,
            email="second@example.com",
            full_name="Second",
            password=STRONG_PASSWORD,
        )
    assert exc_info.value.status == 409
    assert exc_info.value.code == "CONFLICT"


async def test_setup_invalid_email_is_422(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(SETUP_URL, json=_payload(email="not-an-email"))
    assert resp.status_code == 422
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(User)) == 0


async def test_setup_weak_password_is_422_and_no_owner(
    client: AsyncClient, db_session: AsyncSession
):
    resp = await client.post(SETUP_URL, json=_payload(password="password123"))
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "password"
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(User)) == 0
