"""agent_runs journal ops (agent-engine/data-model.html#agent-runs).

One active run per agent — the correctness lock is the partial UNIQUE next to
the journal, never Redis. mark_running additionally gates on the platform-wide
concurrency ceiling (execution.html#concurrency): both facing COUNT(running)
transitions serialize on one advisory lock, killing their write-skew.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.constants import (
    AGENT_GATE_LOCK,
    CODE_AGENT_RUN_ACTIVE,
    AgentRunReason,
    AgentRunState,
)
from achilles.agent_engine.models import AgentRun
from achilles.api.problems import ApiError
from achilles.infra.lifecycle import advisory_xact_lock

_ACTIVE = [str(AgentRunState.QUEUED), str(AgentRunState.RUNNING)]


async def start_run(session: AsyncSession, *, agent_id: int, trigger: str) -> int:
    """Insert a queued run; an unfinished one already holding the agent lock → 409."""
    run = AgentRun(agent_id=agent_id, trigger=trigger)
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409,
            CODE_AGENT_RUN_ACTIVE,
            "Agent run already active",
            "This agent is already running — wait for the current run to finish.",
        ) from exc
    return run.id


async def insert_skipped(
    session: AsyncSession, *, agent_id: int, trigger: str, reason: AgentRunReason
) -> int:
    """Journal a start the runtime gates refused (budget / overlap).

    Terminal on insert — outside the single-flight predicate, so it never
    conflicts with the lock. No started_at: the run never ran.
    """
    run = AgentRun(
        agent_id=agent_id,
        trigger=trigger,
        state=str(AgentRunState.SKIPPED),
        reason=str(reason),
        finished_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run.id


async def mark_running(session: AsyncSession, run_id: int, *, max_concurrency: int) -> bool:
    """Queued → running, unless the platform already runs max_concurrency agents.

    False → either the gate is closed (retry later) or the run is no longer
    queued (reaped) — the caller distinguishes via get_state.
    """
    await advisory_xact_lock(session, AGENT_GATE_LOCK)
    now = datetime.now(UTC)
    running_count = (
        sa.select(sa.func.count())
        .select_from(AgentRun)
        .where(AgentRun.state == str(AgentRunState.RUNNING))
        .scalar_subquery()
    )
    result = await session.execute(
        sa.update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.state == str(AgentRunState.QUEUED),
            running_count < max_concurrency,
        )
        .values(state=str(AgentRunState.RUNNING), started_at=now, heartbeat_at=now)
    )
    return bool(getattr(result, "rowcount", 0))


async def heartbeat(session: AsyncSession, run_id: int) -> None:
    """Beat while active only — queued beats keep a gate-blocked run off the reaper."""
    await session.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.state.in_(_ACTIVE))
        .values(heartbeat_at=datetime.now(UTC))
    )


async def get_state(session: AsyncSession, run_id: int) -> str | None:
    return await session.scalar(sa.select(AgentRun.state).where(AgentRun.id == run_id))


async def finish(
    session: AsyncSession,
    run_id: int,
    *,
    state: AgentRunState,
    reason: AgentRunReason | None = None,
    output: str | None = None,
    tokens_used: int | None = None,
    error: str | None = None,
) -> bool:
    """Terminal transition from an active state only.

    A reaped run stays failed — the reaper already freed the lock; overwriting
    would falsify the journal under a possibly newer active run.
    """
    values: dict[str, object] = {
        "state": str(state),
        "reason": str(reason) if reason is not None else None,
        "finished_at": datetime.now(UTC),
        "error": error,
    }
    if output is not None:
        values["output"] = output
    if tokens_used is not None:
        values["tokens_used"] = tokens_used
    result = await session.execute(
        sa.update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.state.in_(_ACTIVE))
        .values(**values)
    )
    return bool(getattr(result, "rowcount", 0))


def runs_query(agent_id: int) -> sa.Select[tuple[AgentRun]]:
    """Journal page source, newest first — feed to keyset_page."""
    return sa.select(AgentRun).where(AgentRun.agent_id == agent_id).order_by(AgentRun.id.desc())


async def last_run_map(session: AsyncSession, agent_ids: list[int]) -> dict[int, AgentRun]:
    """Latest journal row per agent (any state) — the card's "last run" line."""
    if not agent_ids:
        return {}
    rows = await session.scalars(
        sa.select(AgentRun)
        .distinct(AgentRun.agent_id)
        .where(AgentRun.agent_id.in_(agent_ids))
        .order_by(AgentRun.agent_id, AgentRun.id.desc())
    )
    return {run.agent_id: run for run in rows}
