"""Per-user API rate limit: role tiers, headers, isolation from the brute barrier."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import KEYS_URL
from tests.conftest import ClientFactory
from tests.factories.users import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

LOGIN_URL = "/api/v1/auth/login"


async def test_bucket_exhaustion_answers_429(
    client_factory: ClientFactory, db_session: AsyncSession
):
    user = await create_user(db_session)
    async with client_factory(api_rate_limit_rpm_member=3) as client:
        body = (
            await client.post(LOGIN_URL, json={"email": user.email, "password": DEFAULT_PASSWORD})
        ).json()
        client.headers["Authorization"] = f"Bearer {body['access_token']}"

        statuses = [(await client.get(KEYS_URL)).status_code for _ in range(4)]
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3] == 429


async def test_remaining_header_counts_down(
    client_factory: ClientFactory, db_session: AsyncSession
):
    user = await create_user(db_session)
    async with client_factory(api_rate_limit_rpm_member=5) as client:
        body = (
            await client.post(LOGIN_URL, json={"email": user.email, "password": DEFAULT_PASSWORD})
        ).json()
        client.headers["Authorization"] = f"Bearer {body['access_token']}"

        first = await client.get(KEYS_URL)
        second = await client.get(KEYS_URL)
        assert int(second.headers["X-RateLimit-Remaining"]) < int(
            first.headers["X-RateLimit-Remaining"]
        )


async def test_admin_tier_is_separate(client_factory: ClientFactory, db_session: AsyncSession):
    admin = await create_user(db_session, role="admin")
    async with client_factory(api_rate_limit_rpm_member=1, api_rate_limit_rpm_admin=10) as client:
        body = (
            await client.post(LOGIN_URL, json={"email": admin.email, "password": DEFAULT_PASSWORD})
        ).json()
        client.headers["Authorization"] = f"Bearer {body['access_token']}"
        statuses = [(await client.get(KEYS_URL)).status_code for _ in range(3)]
        assert statuses == [200, 200, 200], "admin tier, not the member ceiling"


async def test_api_limit_does_not_touch_brute_counter(
    client_factory: ClientFactory, db_session: AsyncSession
):
    """Exhausting the API bucket must not poison the login barrier."""
    user = await create_user(db_session)
    async with client_factory(api_rate_limit_rpm_member=1) as client:
        body = (
            await client.post(LOGIN_URL, json={"email": user.email, "password": DEFAULT_PASSWORD})
        ).json()
        client.headers["Authorization"] = f"Bearer {body['access_token']}"
        await client.get(KEYS_URL)
        assert (await client.get(KEYS_URL)).status_code == 429

        # A fresh login still works: different counters entirely.
        client.headers.pop("Authorization")
        resp = await client.post(
            LOGIN_URL, json={"email": user.email, "password": DEFAULT_PASSWORD}
        )
        assert resp.status_code == 200
