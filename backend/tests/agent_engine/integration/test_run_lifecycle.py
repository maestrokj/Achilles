"""agent_runs journal: single-flight lock, concurrency gate, reaper, terminals (P0)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import runs
from achilles.agent_engine.constants import AgentRunReason, AgentRunState, AgentRunTrigger
from achilles.agent_engine.models import AgentRun
from achilles.api.problems import ApiError
from achilles.infra.lifecycle import reap_stale_runs
from tests.factories.agents import create_agent
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def _queued(session: AsyncSession, agent_id: int) -> int:
    run_id = await runs.start_run(session, agent_id=agent_id, trigger=str(AgentRunTrigger.MANUAL))
    await session.commit()
    return run_id


async def test_full_cycle_queued_running_succeeded(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    run_id = await _queued(db_session, agent.id)

    assert await runs.mark_running(db_session, run_id, max_concurrency=4) is True
    await db_session.commit()
    assert await runs.finish(
        db_session, run_id, state=AgentRunState.SUCCEEDED, output="report", tokens_used=150
    )
    await db_session.commit()

    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.SUCCEEDED)
    assert run.output == "report"
    assert run.tokens_used == 150
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.reason is None


async def test_second_start_on_same_agent_is_409(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    await _queued(db_session, agent.id)

    with pytest.raises(ApiError) as exc_info:
        await runs.start_run(db_session, agent_id=agent.id, trigger=str(AgentRunTrigger.MANUAL))
    assert exc_info.value.status == 409
    await db_session.rollback()


async def test_lock_is_per_agent(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    first = await create_agent(db_session, user_id=user.id)
    second = await create_agent(db_session, user_id=user.id)

    await _queued(db_session, first.id)
    assert await _queued(db_session, second.id)  # no 409 — different agent


async def test_skipped_is_terminal_and_outside_the_lock(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    await _queued(db_session, agent.id)  # the lock is held

    # A skipped row inserts fine next to an active run — outside the predicate.
    skipped_id = await runs.insert_skipped(
        db_session,
        agent_id=agent.id,
        trigger=str(AgentRunTrigger.SCHEDULED),
        reason=AgentRunReason.ALREADY_RUNNING,
    )
    await db_session.commit()

    run = await db_session.get_one(AgentRun, skipped_id)
    assert run.state == str(AgentRunState.SKIPPED)
    assert run.reason == str(AgentRunReason.ALREADY_RUNNING)
    assert run.started_at is None
    assert run.finished_at is not None
    assert run.tokens_used == 0


async def test_concurrency_gate_holds_the_excess_run_in_queued(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session)
    first = await create_agent(db_session, user_id=user.id)
    second = await create_agent(db_session, user_id=user.id)
    first_run = await _queued(db_session, first.id)
    second_run = await _queued(db_session, second.id)

    assert await runs.mark_running(db_session, first_run, max_concurrency=1) is True
    await db_session.commit()
    # The ceiling is platform-wide: the second agent's run waits its turn.
    assert await runs.mark_running(db_session, second_run, max_concurrency=1) is False
    await db_session.rollback()
    assert await runs.get_state(db_session, second_run) == str(AgentRunState.QUEUED)

    # The first run finishing frees a slot.
    await runs.finish(db_session, first_run, state=AgentRunState.SUCCEEDED, output="")
    await db_session.commit()
    assert await runs.mark_running(db_session, second_run, max_concurrency=1) is True
    await db_session.rollback()


async def test_reaper_fails_a_zombie_with_reason_stale(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent_id = (await create_agent(db_session, user_id=user.id)).id
    run_id = await _queued(db_session, agent_id)
    assert await runs.mark_running(db_session, run_id, max_concurrency=4)
    stale = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.execute(
        sa.update(AgentRun).where(AgentRun.id == run_id).values(heartbeat_at=stale)
    )
    await db_session.commit()

    swept = await reap_stale_runs(db_session)
    await db_session.commit()

    assert swept >= 1
    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.FAILED)
    assert run.reason == str(AgentRunReason.STALE)
    assert run.error == "heartbeat lost"
    assert run.finished_at is not None
    # The lock is free again: a new run starts without a 409.
    assert await _queued(db_session, agent_id)


async def test_finish_is_terminal_only_from_active(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    run_id = await _queued(db_session, agent.id)
    assert await runs.finish(
        db_session, run_id, state=AgentRunState.FAILED, reason=AgentRunReason.ERROR, error="x"
    )
    await db_session.commit()

    # A second terminal write must not falsify the journal.
    assert (
        await runs.finish(db_session, run_id, state=AgentRunState.SUCCEEDED, output="late") is False
    )
    await db_session.rollback()
    assert await runs.get_state(db_session, run_id) == str(AgentRunState.FAILED)


async def test_heartbeat_skips_terminal_states(db_session: AsyncSession) -> None:
    user = await create_user(db_session)
    agent = await create_agent(db_session, user_id=user.id)
    run_id = await _queued(db_session, agent.id)
    await runs.finish(db_session, run_id, state=AgentRunState.FAILED, reason=AgentRunReason.ERROR)
    await db_session.commit()

    await runs.heartbeat(db_session, run_id)
    await db_session.commit()
    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.heartbeat_at is None
