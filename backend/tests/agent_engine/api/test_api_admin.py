"""Admin surface: registry, read-only profile, the pause lever, run limits (P0)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.models import Agent
from achilles.ai_foundation.models import AiModel
from achilles.auth.constants import UserRole
from achilles.auth.models import AuditLog
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.agents import (
    allow_agent_model,
    create_agent,
    create_run,
    set_agent_budget,
)
from tests.factories.users import User, create_user

pytestmark = [pytest.mark.api, pytest.mark.p0]

ADMIN_AGENTS = "/api/v1/admin/agents"
LIMITS = "/api/v1/admin/agent-limits"


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> User:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)
    return admin


async def test_registry_lists_all_owners_with_search_and_facets(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    alice = await create_user(db_session, email="alice@example.com")
    bob = await create_user(db_session, email="bob@example.com")
    digest = await create_agent(db_session, user_id=alice.id, name="Weekly digest")
    paused = await create_agent(db_session, user_id=bob.id, name="Paused one", admin_paused=True)

    resp = await client.get(ADMIN_AGENTS)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {item["id"] for item in items} == {digest.id, paused.id}
    assert {item["owner"]["email"] for item in items} == {alice.email, bob.email}

    by_name = await client.get(ADMIN_AGENTS, params={"q": "weekly"})
    assert [i["id"] for i in by_name.json()["items"]] == [digest.id]

    by_owner = await client.get(ADMIN_AGENTS, params={"q": "bob@"})
    assert [i["id"] for i in by_owner.json()["items"]] == [paused.id]

    by_status = await client.get(ADMIN_AGENTS, params={"status": "admin_paused"})
    assert [i["id"] for i in by_status.json()["items"]] == [paused.id]

    disabled = await create_agent(db_session, user_id=alice.id, name="Off", enabled=False)
    scheduled = await create_agent(
        db_session,
        user_id=bob.id,
        name="Cron",
        schedule={"type": "interval", "every_hours": 6},
    )
    by_disabled = await client.get(ADMIN_AGENTS, params={"status": "disabled"})
    assert [i["id"] for i in by_disabled.json()["items"]] == [disabled.id]
    # Statuses combine as OR — paused and disabled both come back.
    by_or = await client.get(
        ADMIN_AGENTS, params=[("status", "admin_paused"), ("status", "disabled")]
    )
    assert {i["id"] for i in by_or.json()["items"]} == {paused.id, disabled.id}
    by_schedule = await client.get(ADMIN_AGENTS, params={"scheduled": "true"})
    assert [i["id"] for i in by_schedule.json()["items"]] == [scheduled.id]

    # A seed row can store the manual schedule as a JSONB 'null' literal, not
    # SQL NULL — the manual filter must still catch it.
    jsonb_null = await create_agent(db_session, user_id=alice.id, name="Manual")
    await db_session.execute(
        sa.update(Agent).where(Agent.id == jsonb_null.id).values(schedule=sa.text("'null'::jsonb"))
    )
    await db_session.commit()
    manual_ids = {
        i["id"]
        for i in (await client.get(ADMIN_AGENTS, params={"scheduled": "false"})).json()["items"]
    }
    assert jsonb_null.id in manual_ids
    assert scheduled.id not in manual_ids
    scheduled_only = await client.get(ADMIN_AGENTS, params={"scheduled": "true"})
    assert {i["id"] for i in scheduled_only.json()["items"]} == {scheduled.id}


async def test_registry_filters_budget_derived_status(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    """The budget-dependent statuses fold in the per-owner weekly spend."""
    thrifty = await create_user(db_session, email="thrifty@example.com")
    spender = await create_user(db_session, email="spender@example.com")
    model = await allow_agent_model(db_session)
    lean = await create_agent(db_session, user_id=thrifty.id, model_id=model.id)
    over = await create_agent(db_session, user_id=spender.id, model_id=model.id)
    await set_agent_budget(db_session, 1000)
    await create_run(db_session, agent_id=lean.id, tokens_used=100)
    await create_run(db_session, agent_id=over.id, tokens_used=1500)

    exceeded = await client.get(ADMIN_AGENTS, params={"status": "budget_exceeded"})
    assert [i["id"] for i in exceeded.json()["items"]] == [over.id]
    active = await client.get(ADMIN_AGENTS, params={"status": "active"})
    assert [i["id"] for i in active.json()["items"]] == [lean.id]


async def test_registry_paginates(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    owner = await create_user(db_session)
    for _ in range(3):
        await create_agent(db_session, user_id=owner.id)

    first = await client.get(ADMIN_AGENTS, params={"per_page": 25, "page": 1})
    page = first.json()
    assert len(page["items"]) == 3
    assert page["total"] == 3
    assert page["page"] == 1
    assert page["per_page"] == 25
    second = await client.get(ADMIN_AGENTS, params={"per_page": 25, "page": 2})
    # One page holds all three — the past-the-end request clamps back to page 1.
    assert second.json()["page"] == 1


async def test_profile_is_full_and_carries_the_owner_budget(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    owner = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(db_session, user_id=owner.id, model_id=allowed.id)
    await create_run(db_session, agent_id=agent.id, tokens_used=42)

    model_name = await db_session.scalar(
        sa.select(AiModel.display_name).where(AiModel.id == allowed.model_id)
    )

    resp = await client.get(f"{ADMIN_AGENTS}/{agent.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["prompt"] == agent.prompt
    assert body["model_name"] == model_name  # resolved server-side, never a raw PK
    assert body["tools"] == []
    assert body["owner"]["id"] == owner.id
    assert body["owner_budget"]["used"] == 42
    assert body["owner_budget"]["limit"] is None  # no ceiling configured

    runs_resp = await client.get(f"{ADMIN_AGENTS}/{agent.id}/runs")
    assert runs_resp.status_code == 200
    assert len(runs_resp.json()["items"]) == 1


async def test_pause_toggle_is_the_only_lever_and_is_audited(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    owner = await create_user(db_session)
    agent = await create_agent(db_session, user_id=owner.id)

    resp = await client.patch(f"{ADMIN_AGENTS}/{agent.id}/pause", json={"paused": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["admin_paused"] is True
    assert resp.json()["status"] == "admin_paused"

    resp = await client.patch(f"{ADMIN_AGENTS}/{agent.id}/pause", json={"paused": False})
    assert resp.json()["admin_paused"] is False

    audit = list(
        await db_session.scalars(sa.select(AuditLog).where(AuditLog.action == "agent.pause"))
    )
    assert len(audit) == 2

    # No admin DELETE/PATCH of a foreign agent exists on this surface.
    assert (await client.delete(f"{ADMIN_AGENTS}/{agent.id}")).status_code == 405


async def test_limits_get_and_patch(
    client: AsyncClient, db_session: AsyncSession, as_admin: User
) -> None:
    resp = await client.get(LIMITS)
    assert resp.status_code == 200
    assert resp.json() == {"iteration_cap": 15, "max_concurrency": 4}

    resp = await client.patch(LIMITS, json={"iteration_cap": 30})
    assert resp.status_code == 200
    assert resp.json() == {"iteration_cap": 30, "max_concurrency": 4}

    assert (await client.patch(LIMITS, json={"iteration_cap": 0})).status_code == 422
    assert (await client.patch(LIMITS, json={"max_concurrency": -1})).status_code == 422

    audit = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "agent.limits_update")
    )
    assert audit == 1


async def test_member_is_403_on_the_admin_surface(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
) -> None:
    member = await create_user(db_session)
    other = await create_user(db_session)
    agent = await create_agent(db_session, user_id=other.id)
    await authorize(member.email)

    for method, url in (
        ("get", ADMIN_AGENTS),
        ("get", f"{ADMIN_AGENTS}/{agent.id}"),
        ("get", f"{ADMIN_AGENTS}/{agent.id}/runs"),
        ("patch", f"{ADMIN_AGENTS}/{agent.id}/pause"),
        ("get", LIMITS),
        ("patch", LIMITS),
    ):
        resp = await client.request(method, url, json={"paused": True} if "pause" in url else {})
        assert resp.status_code == 403, (method, url, resp.status_code)


async def test_admin_pause_survives_owner_enable_flip(
    client: AsyncClient, db_session: AsyncSession, as_admin: User, authorize: AuthorizeFn
) -> None:
    owner = await create_user(db_session)
    agent_id = (await create_agent(db_session, user_id=owner.id)).id
    resp = await client.patch(f"{ADMIN_AGENTS}/{agent_id}/pause", json={"paused": True})
    assert resp.status_code == 200

    # The owner flips their toggle — the sticky admin lock stays.
    await authorize(owner.email)
    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"enabled": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["admin_paused"] is True
    assert body["status"] == "admin_paused"
    db_session.expire_all()
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.admin_paused is True
