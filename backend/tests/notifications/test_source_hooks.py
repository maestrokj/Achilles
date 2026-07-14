"""Source wiring: the catalog events actually fire where the design says (API)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.notifications.models import Notification
from tests.auth.integration.conftest import AuthorizeFn, LoginFn, Outbox
from tests.factories.agents import create_agent
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def _events(session: AsyncSession) -> list[tuple[str, int | None]]:
    rows = await session.execute(
        sa.select(Notification.title, Notification.target_user_id).order_by(Notification.id)
    )
    return [(title, target) for title, target in rows]


async def test_role_change_raises_broadcast_and_targeted(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    await authorize(owner.email)

    resp = await client.patch(f"/api/v1/admin/users/{member.id}", json={"role": "admin"})
    assert resp.status_code == 200

    events = await _events(db_session)
    assert ("security.role_changed", None) in events, "the org-security broadcast"
    assert ("account.role_changed", member.id) in events, "the personal note"


async def test_status_change_raises_nothing(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    await authorize(owner.email)

    resp = await client.patch(f"/api/v1/admin/users/{member.id}", json={"status": "deactivated"})
    assert resp.status_code == 200
    assert await _events(db_session) == []


async def test_temp_password_reset_notifies_the_target(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await authorize(admin.email)

    resp = await client.post(f"/api/v1/admin/users/{member.id}/reset-password")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "temp_password"
    assert ("account.temp_password", member.id) in await _events(db_session)


async def test_brute_force_alert_enqueued_at_the_threshold(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, outbox: Outbox
):
    user = await create_user(db_session)
    # The exponential delay gates real attempts — step the clock past it each time.
    with time_machine.travel(datetime.now(UTC), tick=False) as traveller:
        for _ in range(11):
            assert (await login(user.email, "wrong-password")).status_code == 401
            traveller.shift(timedelta(seconds=31))

    raised = [job for job in outbox.jobs if job.function == "raise_event"]
    assert len(raised) == 1, "exactly once — at the threshold, not on every failure"
    assert raised[0].kwargs["event"] == "security.brute_force"
    assert raised[0].kwargs["params"] == {"email": user.email}


async def test_admin_pause_notifies_the_owner(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    agent = await create_agent(db_session, user_id=member.id, name="Watcher")
    await authorize(owner.email)

    resp = await client.patch(f"/api/v1/admin/agents/{agent.id}/pause", json={"paused": True})
    assert resp.status_code == 200, resp.text
    assert ("agent.admin_paused", member.id) in await _events(db_session)

    # lifting the pause is banner news, not a feed event
    resp = await client.patch(f"/api/v1/admin/agents/{agent.id}/pause", json={"paused": False})
    assert resp.status_code == 200
    events = await _events(db_session)
    assert len([e for e in events if e[0] == "agent.admin_paused"]) == 1
