"""Gate axes, sticky admin pause, owner lifecycle cascades (P0, governance.html)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import service
from achilles.agent_engine.constants import AgentStatus, ScheduleKind
from achilles.agent_engine.models import Agent, AgentRun, AgentTool
from achilles.agent_engine.schemas import AgentPatch
from achilles.ai_foundation.models import AgentModel
from achilles.auth.services.users_admin import deactivate_cascade
from achilles.knowledge_store.services.platform import get_platform_settings
from tests.factories.agents import allow_agent_model, create_agent, create_run
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

NOW = datetime.now(UTC)
UTC_TZ = ZoneInfo("UTC")
INTERVAL_2H = {"type": str(ScheduleKind.INTERVAL), "every_hours": 2}


def test_status_priority_order() -> None:
    agent = Agent(user_id=1, name="a", prompt="p", model_id=1, enabled=True, admin_paused=False)
    assert service.derive_status(agent, over_budget=False, model_live=True) == AgentStatus.ACTIVE
    assert (
        service.derive_status(agent, over_budget=True, model_live=True)
        == AgentStatus.BUDGET_EXCEEDED
    )
    # A paused/removed model closes the gate even while model_id still points at it.
    assert (
        service.derive_status(agent, over_budget=True, model_live=False)
        == AgentStatus.MODEL_MISSING
    )
    agent.enabled = False
    assert service.derive_status(agent, over_budget=False, model_live=True) == AgentStatus.DISABLED
    agent.admin_paused = True
    assert (
        service.derive_status(agent, over_budget=False, model_live=True) == AgentStatus.ADMIN_PAUSED
    )


def test_gate_axes_are_independent() -> None:
    agent = Agent(user_id=1, name="a", prompt="p", model_id=1, enabled=True, admin_paused=False)
    assert service.durable_stop(agent, model_live=True) is False
    for attr, closed in (("enabled", False), ("admin_paused", True)):
        setattr(agent, attr, closed)
        assert service.durable_stop(agent, model_live=True) is True
        setattr(agent, attr, {"enabled": True, "admin_paused": False}[attr])
    # The model axis rides on liveness, not on model_id being NULL: a paused
    # allow-list entry closes the gate with model_id still set.
    assert service.durable_stop(agent, model_live=False) is True


def test_owner_patch_cannot_touch_the_admin_lock() -> None:
    assert "admin_paused" not in AgentPatch.model_fields


async def test_admin_pause_is_sticky_and_clears_the_slot(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(
        db_session, user_id=user.id, model_id=allowed.id, schedule=INTERVAL_2H
    )
    service.recompute_next_run(agent, owner_tz=UTC_TZ, now=NOW, model_live=True)
    await db_session.commit()
    assert agent.next_run_at is not None

    await service.set_admin_pause(db_session, agent=agent, paused=True, owner_tz=UTC_TZ, now=NOW)
    await db_session.commit()
    assert agent.admin_paused is True
    assert agent.next_run_at is None

    # The same toggle lifts the lock and the schedule comes back.
    await service.set_admin_pause(db_session, agent=agent, paused=False, owner_tz=UTC_TZ, now=NOW)
    await db_session.commit()
    assert agent.admin_paused is False
    assert agent.next_run_at is not None


async def test_deactivation_disables_agents_and_reactivation_does_not_return_them(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    first = await create_agent(
        db_session, user_id=user.id, model_id=allowed.id, schedule=INTERVAL_2H
    )
    first.next_run_at = NOW
    second = await create_agent(db_session, user_id=user.id, model_id=allowed.id)
    await db_session.commit()
    agent_ids = (first.id, second.id)

    await deactivate_cascade(db_session, user)
    await db_session.commit()

    db_session.expire_all()
    for agent_id in agent_ids:
        agent = await db_session.get_one(Agent, agent_id)
        assert agent.enabled is False
        assert agent.next_run_at is None
    # Reactivation is an account-level act; agents stay off until the owner
    # flips them personally — nothing here re-enables them.


async def test_user_delete_cascades_agents_and_journal(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    agent_id = agent.id
    await create_run(db_session, agent_id=agent_id, tokens_used=10)

    await db_session.delete(user)
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(Agent, agent_id) is None
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(AgentRun))) == 0


async def test_model_removal_nulls_the_reference(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent_id = (await create_agent(db_session, user_id=user.id, model_id=allowed.id)).id

    await db_session.execute(sa.delete(AgentModel).where(AgentModel.id == allowed.id))
    await db_session.commit()

    db_session.expire_all()
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.model_id is None  # ON DELETE SET NULL — the gate closes
    model_live = await service.model_is_live(db_session, refreshed.model_id)
    assert service.durable_stop(refreshed, model_live=model_live) is True


async def test_paused_model_closes_gate_while_keeping_the_reference(
    db_session: AsyncSession,
) -> None:
    """Pausing an allow-list entry (is_enabled=false, row kept) must read as
    model-missing — the run path fails it, so the gate/status must agree."""
    user = await create_user(db_session)
    allowed_id = (await allow_agent_model(db_session)).id
    agent_id = (await create_agent(db_session, user_id=user.id, model_id=allowed_id)).id

    await db_session.execute(
        sa.update(AgentModel).where(AgentModel.id == allowed_id).values(is_enabled=False)
    )
    await db_session.commit()

    db_session.expire_all()
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.model_id == allowed_id  # reference kept — re-enable restores it
    model_live = await service.model_is_live(db_session, refreshed.model_id)
    assert model_live is False
    assert service.durable_stop(refreshed, model_live=model_live) is True
    assert (
        service.derive_status(refreshed, over_budget=False, model_live=model_live)
        == AgentStatus.MODEL_MISSING
    )


async def test_patch_survives_an_echoed_paused_model(db_session: AsyncSession) -> None:
    """Editing a model-missing agent must not 422: the editor always echoes
    model_id, so an unchanged (now-paused) selection has to skip re-validation."""
    user = await create_user(db_session)
    allowed_id = (await allow_agent_model(db_session)).id
    agent = await create_agent(db_session, user_id=user.id, model_id=allowed_id)
    await db_session.execute(
        sa.update(AgentModel).where(AgentModel.id == allowed_id).values(is_enabled=False)
    )
    await db_session.commit()

    platform = await get_platform_settings(db_session)
    patched = await service.patch_agent(
        db_session,
        user=user,
        agent=agent,
        body=AgentPatch(name="Renamed", model_id=allowed_id),
        platform=platform,
        now=NOW,
    )
    assert patched.name == "Renamed"
    assert patched.model_id == allowed_id  # the paused reference is kept, not rejected


async def test_agent_delete_cascades_tools_and_runs(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    await create_run(db_session, agent_id=agent.id)

    await db_session.delete(agent)
    await db_session.commit()

    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(AgentRun))) == 0
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(AgentTool))) == 0
