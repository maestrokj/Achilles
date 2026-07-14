"""CSRF v1: Origin/Referer check on mutations — tests.html (P1)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_ORIGIN
from tests.factories.users import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

LOGIN_URL = "/api/v1/auth/login"


async def _login_with_headers(client: AsyncClient, email: str, headers: dict[str, str]):
    return await client.post(
        LOGIN_URL, json={"email": email, "password": DEFAULT_PASSWORD}, headers=headers
    )


async def test_mismatched_origin_is_403(client: AsyncClient, db_session: AsyncSession):
    user = await create_user(db_session)
    resp = await _login_with_headers(client, user.email, {"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


async def test_valid_origin_passes(client: AsyncClient, db_session: AsyncSession):
    user = await create_user(db_session)
    resp = await _login_with_headers(client, user.email, {"Origin": TEST_ORIGIN})
    assert resp.status_code == 200


async def test_no_origin_no_referer_passes(client: AsyncClient, db_session: AsyncSession):
    """Non-browser clients send neither header — they must not be locked out."""
    user = await create_user(db_session)
    resp = await _login_with_headers(client, user.email, {})
    assert resp.status_code == 200


async def test_mismatched_referer_is_403(client: AsyncClient, db_session: AsyncSession):
    user = await create_user(db_session)
    resp = await _login_with_headers(client, user.email, {"Referer": "https://evil.example/phish"})
    assert resp.status_code == 403


async def test_get_requests_skip_the_check(client: AsyncClient):
    resp = await client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200
