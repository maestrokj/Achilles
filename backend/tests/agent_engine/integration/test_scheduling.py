"""next_run_at lifecycle + the agents_tick scan (P0, execution.html#schedule)."""

from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from redis.asyncio import Redis
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import service
from achilles.agent_engine.constants import (
    AgentRunReason,
    AgentRunState,
    AgentRunTrigger,
    CalendarCadence,
    ScheduleKind,
)
from achilles.agent_engine.models import Agent, AgentRun
from achilles.agent_engine.scheduler import tick
from achilles.agent_engine.schemas import AgentPatch
from achilles.config import Settings
from achilles.knowledge_store.services.platform import get_platform_settings
from tests.factories.admin import set_platform_settings
from tests.factories.agents import (
    allow_agent_model,
    create_agent,
    create_run,
    set_agent_budget,
)
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

CTX = cast("Context", {})
NOW = datetime.now(UTC)
UTC_TZ = ZoneInfo("UTC")

INTERVAL_2H = {"type": str(ScheduleKind.INTERVAL), "every_hours": 2}


@pytest.fixture(autouse=True)
def tick_uses_test_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(tick, "app_settings", test_settings)


async def _runnable_agent(session: AsyncSession, **kwargs: object) -> Agent:
    user = await create_user(session)
    allowed = await allow_agent_model(session)
    return await create_agent(session, user_id=user.id, model_id=allowed.id, **kwargs)


# --- recompute_next_run (service-level) ---


async def test_schedule_null_means_manual_only(db_session: AsyncSession) -> None:
    agent = await _runnable_agent(db_session)
    service.recompute_next_run(agent, owner_tz=UTC_TZ, now=NOW, model_live=True)
    assert agent.next_run_at is None


async def test_interval_schedule_sets_the_scan_key(db_session: AsyncSession) -> None:
    agent = await _runnable_agent(db_session, schedule=INTERVAL_2H)
    service.recompute_next_run(agent, owner_tz=UTC_TZ, now=NOW, model_live=True)
    assert agent.next_run_at == NOW + timedelta(hours=2)


async def test_closed_gate_clears_the_scan_key(db_session: AsyncSession) -> None:
    agent = await _runnable_agent(db_session, schedule=INTERVAL_2H)
    agent.enabled = False
    service.recompute_next_run(agent, owner_tz=UTC_TZ, now=NOW, model_live=True)
    assert agent.next_run_at is None


async def test_unrelated_patch_keeps_the_slot(db_session: AsyncSession) -> None:
    """A rename must not re-anchor an interval schedule and postpone an imminent run."""
    user = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(
        db_session, user_id=user.id, model_id=allowed.id, schedule=INTERVAL_2H
    )
    imminent = NOW + timedelta(minutes=10)
    agent.next_run_at = imminent
    platform = await get_platform_settings(db_session)
    await service.patch_agent(
        db_session,
        user=user,
        agent=agent,
        body=AgentPatch(name="Renamed"),
        platform=platform,
        now=NOW,
    )
    assert agent.next_run_at == imminent


async def test_disabling_patch_clears_the_slot(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(
        db_session, user_id=user.id, model_id=allowed.id, schedule=INTERVAL_2H
    )
    agent.next_run_at = NOW + timedelta(minutes=10)
    platform = await get_platform_settings(db_session)
    await service.patch_agent(
        db_session,
        user=user,
        agent=agent,
        body=AgentPatch(enabled=False),
        platform=platform,
        now=NOW,
    )
    assert agent.next_run_at is None


async def test_calendar_uses_owner_timezone_with_org_fallback(
    db_session: AsyncSession,
) -> None:
    user = await create_user(
        db_session,
    )
    user.timezone = "Europe/Moscow"
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(
        db_session,
        user_id=user.id,
        model_id=allowed.id,
        schedule={
            "type": str(ScheduleKind.CALENDAR),
            "cadence": str(CalendarCadence.DAILY),
            "time": "09:00",
        },
    )
    owner_tz = await service.owner_zone(db_session, user.id, fallback=UTC_TZ)
    assert owner_tz.key == "Europe/Moscow"
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    service.recompute_next_run(agent, owner_tz=owner_tz, now=now, model_live=True)
    # 09:00 Moscow = 06:00 UTC, already past noon UTC → tomorrow; stored as UTC.
    assert agent.next_run_at == datetime(2026, 7, 2, 6, 0, tzinfo=UTC)

    user.timezone = None
    await db_session.commit()
    fallback = await service.owner_zone(db_session, user.id, fallback=UTC_TZ)
    assert fallback.key == "UTC"


# --- agents_tick ---


async def _due(session: AsyncSession, **kwargs: object) -> int:
    agent = await _runnable_agent(session, schedule=INTERVAL_2H, **kwargs)
    agent.next_run_at = NOW - timedelta(minutes=1)
    await session.commit()
    return agent.id


async def test_tick_queues_the_due_agent_and_advances_the_slot(
    db_session: AsyncSession, redis_durable: Redis
) -> None:
    agent_id = await _due(db_session)

    await tick.agents_tick(CTX)

    db_session.expire_all()
    run = await db_session.scalar(sa.select(AgentRun).where(AgentRun.agent_id == agent_id))
    assert run is not None
    assert run.state == str(AgentRunState.QUEUED)
    assert run.trigger == str(AgentRunTrigger.SCHEDULED)
    assert await redis_durable.exists(f"dedup:job:agent:{run.id}")
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > NOW


async def test_tick_pauses_under_org_maintenance(db_session: AsyncSession) -> None:
    agent_id = await _due(db_session)
    await set_platform_settings(db_session, maintenance_mode=True)

    await tick.agents_tick(CTX)

    db_session.expire_all()
    run = await db_session.scalar(sa.select(AgentRun).where(AgentRun.agent_id == agent_id))
    assert run is None, "org maintenance pauses scheduled launches"


async def test_tick_skips_an_overlapping_run(db_session: AsyncSession) -> None:
    agent_id = await _due(db_session)
    await create_run(db_session, agent_id=agent_id, state=AgentRunState.RUNNING, finished_ago=None)

    await tick.agents_tick(CTX)

    db_session.expire_all()
    states = list(
        await db_session.scalars(
            sa.select(AgentRun.state).where(AgentRun.agent_id == agent_id).order_by(AgentRun.id)
        )
    )
    assert states == [str(AgentRunState.RUNNING), str(AgentRunState.SKIPPED)]
    skipped = await db_session.scalar(
        sa.select(AgentRun).where(
            AgentRun.agent_id == agent_id, AgentRun.state == str(AgentRunState.SKIPPED)
        )
    )
    assert skipped is not None
    assert skipped.reason == str(AgentRunReason.ALREADY_RUNNING)
    # The slot still advanced — no re-fire storm on the next scan minute.
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > NOW


async def test_tick_skips_on_exhausted_budget(db_session: AsyncSession) -> None:
    agent_id = await _due(db_session)
    await create_run(db_session, agent_id=agent_id, tokens_used=100)
    await set_agent_budget(db_session, 50)

    await tick.agents_tick(CTX)

    db_session.expire_all()
    skipped = await db_session.scalar(
        sa.select(AgentRun).where(
            AgentRun.agent_id == agent_id, AgentRun.state == str(AgentRunState.SKIPPED)
        )
    )
    assert skipped is not None
    assert skipped.reason == str(AgentRunReason.BUDGET_EXCEEDED)


async def test_sweep_clears_the_key_when_the_model_is_gone(db_session: AsyncSession) -> None:
    """agent_models deletion runs SET NULL inside Postgres — the sweep catches up."""
    agent_id = await _due(db_session)
    await db_session.execute(sa.update(Agent).where(Agent.id == agent_id).values(model_id=None))
    await db_session.commit()

    await tick.agents_tick(CTX)

    db_session.expire_all()
    refreshed = await db_session.get_one(Agent, agent_id)
    assert refreshed.next_run_at is None
    # Quiet: a durable stop leaves no journal noise.
    assert (
        await db_session.scalar(
            sa.select(sa.func.count()).select_from(AgentRun).where(AgentRun.agent_id == agent_id)
        )
    ) == 0


async def test_tick_ignores_agents_without_a_due_slot(db_session: AsyncSession) -> None:
    agent = await _runnable_agent(db_session, schedule=INTERVAL_2H)
    agent.next_run_at = NOW + timedelta(hours=1)
    await db_session.commit()

    await tick.agents_tick(CTX)

    assert (
        await db_session.scalar(
            sa.select(sa.func.count()).select_from(AgentRun).where(AgentRun.agent_id == agent.id)
        )
    ) == 0
