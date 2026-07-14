"""Derived weekly budget: SUM over the journal, calendar week in org time (P0)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import runs, service
from achilles.agent_engine.constants import AgentRunReason, AgentRunState
from achilles.agent_engine.models import AgentRun
from achilles.api.problems import ApiError
from achilles.knowledge_store.services.platform import get_platform_settings
from tests.factories.agents import (
    allow_agent_model,
    create_agent,
    create_run,
    set_agent_budget,
)
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

NOW = datetime.now(UTC)


async def test_weekly_spend_sums_all_owner_agents_in_window(db_session: AsyncSession) -> None:
    owner = await create_user(db_session)
    stranger = await create_user(db_session)
    first = await create_agent(db_session, user_id=owner.id)
    second = await create_agent(db_session, user_id=owner.id)
    foreign = await create_agent(db_session, user_id=stranger.id)

    await create_run(db_session, agent_id=first.id, tokens_used=100)
    await create_run(db_session, agent_id=second.id, tokens_used=50)
    await create_run(db_session, agent_id=foreign.id, tokens_used=999)  # not the owner's
    # Out of window: finished before the window start.
    await create_run(db_session, agent_id=first.id, tokens_used=777, finished_ago=timedelta(days=8))
    # An unfinished run joins the sum only at its finale.
    await create_run(
        db_session,
        agent_id=first.id,
        state=AgentRunState.RUNNING,
        tokens_used=0,
        finished_ago=None,
    )

    since = NOW - timedelta(days=7)
    assert await service.weekly_spend(db_session, user_id=owner.id, since=since) == 150


def test_weekly_window_resets_sunday_midnight_org_time() -> None:
    # Wednesday 2026-07-01 12:00 UTC → the window opened Sunday 2026-06-28.
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert service.weekly_window_start(now, ZoneInfo("UTC")) == datetime(
        2026, 6, 28, 0, 0, tzinfo=UTC
    )
    # New York midnight is 04:00 UTC that day.
    assert service.weekly_window_start(now, ZoneInfo("America/New_York")) == datetime(
        2026, 6, 28, 4, 0, tzinfo=UTC
    )
    # Sunday itself belongs to the new window.
    sunday = datetime(2026, 6, 28, 0, 30, tzinfo=UTC)
    assert service.weekly_window_start(sunday, ZoneInfo("UTC")) == datetime(
        2026, 6, 28, 0, 0, tzinfo=UTC
    )


def test_budget_exceeded_boundary() -> None:
    assert service.budget_exceeded(100, 100) is True  # at the ceiling = exhausted
    assert service.budget_exceeded(99, 100) is False
    assert service.budget_exceeded(10**9, None) is False  # NULL = no ceiling


async def test_limit_is_read_from_platform_settings(db_session: AsyncSession) -> None:
    owner = await create_user(db_session)
    await set_agent_budget(db_session, 2_000_000)
    platform_row = await get_platform_settings(db_session)
    used, limit, resets_at = await service.budget_snapshot(
        db_session, user_id=owner.id, platform=platform_row, now=NOW
    )
    assert used == 0
    assert limit == 2_000_000
    assert resets_at > NOW


async def test_manual_run_over_budget_journals_skipped_and_409(
    db_session: AsyncSession,
) -> None:
    owner = await create_user(db_session)
    agent = await create_agent(db_session, user_id=owner.id)
    allowed = await allow_agent_model(db_session)
    agent.model_id = allowed.id
    await db_session.commit()
    await create_run(db_session, agent_id=agent.id, tokens_used=100)
    await set_agent_budget(db_session, 100)
    platform_row = await get_platform_settings(db_session)

    with pytest.raises(ApiError) as exc_info:
        await service.gate_manual_run(db_session, agent=agent, platform=platform_row, now=NOW)

    assert exc_info.value.status == 409
    assert exc_info.value.code == "AGENT_BUDGET_EXCEEDED"
    skipped = await db_session.scalar(
        sa.select(AgentRun).where(
            AgentRun.agent_id == agent.id, AgentRun.state == str(AgentRunState.SKIPPED)
        )
    )
    assert skipped is not None
    assert skipped.reason == str(AgentRunReason.BUDGET_EXCEEDED)
    assert skipped.tokens_used == 0
    # No queued row appeared alongside the refusal.
    assert (
        await db_session.scalar(
            sa.select(sa.func.count())
            .select_from(AgentRun)
            .where(AgentRun.agent_id == agent.id, AgentRun.state == str(AgentRunState.QUEUED))
        )
    ) == 0


async def test_manual_run_within_budget_queues(db_session: AsyncSession) -> None:
    owner = await create_user(db_session)
    allowed = await allow_agent_model(db_session)
    agent = await create_agent(db_session, user_id=owner.id, model_id=allowed.id)
    await set_agent_budget(db_session, 1_000)
    platform_row = await get_platform_settings(db_session)

    run_id = await service.gate_manual_run(db_session, agent=agent, platform=platform_row, now=NOW)
    await db_session.commit()
    assert await runs.get_state(db_session, run_id) == str(AgentRunState.QUEUED)
