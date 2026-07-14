"""Org maintenance mode: members wait out a 503, Owner/Admin keep working."""

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.admin import maintenance
from achilles.auth.constants import UserRole
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

SETTINGS_URL = "/api/v1/admin/settings"
# Any member-accessible authenticated route works as the probe.
PROBE_URL = "/api/v1/chat/models"


async def test_member_waits_out_maintenance(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role=UserRole.OWNER.value)
    member = await create_user(db_session, role=UserRole.MEMBER.value)

    await authorize(member.email)
    assert (await client.get(PROBE_URL)).status_code == 200

    await authorize(owner.email)
    assert (await client.patch(SETTINGS_URL, json={"maintenance_mode": True})).status_code == 200
    assert (await client.get(SETTINGS_URL)).status_code == 200, "the Owner keeps working"

    # Login stays open (someone must be able to reach the client at all)…
    resp = await authorize(member.email)
    assert resp.status_code == 200
    # …but the first real call answers 503 with a Retry-After.
    blocked = await client.get(PROBE_URL)
    assert blocked.status_code == 503
    assert "Retry-After" in blocked.headers

    await authorize(owner.email)
    assert (await client.patch(SETTINGS_URL, json={"maintenance_mode": False})).status_code == 200
    await authorize(member.email)
    assert (await client.get(PROBE_URL)).status_code == 200


async def test_admin_passes_during_maintenance(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role=UserRole.OWNER.value)
    admin = await create_user(db_session, role=UserRole.ADMIN.value)

    await authorize(owner.email)
    await client.patch(SETTINGS_URL, json={"maintenance_mode": True})

    await authorize(admin.email)
    assert (await client.get(SETTINGS_URL)).status_code == 200


async def test_toggle_is_owner_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)
    resp = await client.patch(SETTINGS_URL, json={"maintenance_mode": True})
    assert resp.status_code == 403


async def test_db_row_reseeds_the_redis_mirror(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    redis_durable: Redis,
):
    """A redis wipe loses the flag; the lifespan sync restores it from the DB row."""
    owner = await create_user(db_session, role=UserRole.OWNER.value)
    member = await create_user(db_session, role=UserRole.MEMBER.value)

    await authorize(owner.email)
    await client.patch(SETTINGS_URL, json={"maintenance_mode": True})

    await redis_durable.flushdb()  # type: ignore[misc]
    await maintenance.sync_from_db(db_session, redis_durable)

    await authorize(member.email)
    assert (await client.get(PROBE_URL)).status_code == 503
