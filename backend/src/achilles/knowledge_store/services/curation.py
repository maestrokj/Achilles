"""curation_runs journal ops + the destructive lane gate (lifecycle.html).

One unfinished platform run at a time — the correctness lock is the partial
UNIQUE next to the journal, never Redis. The destructive window
(destructive_since) is the other half of lane coordination: while it is open
on a live running run, sync mark_running yields (#coordination).
"""

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.infra.lifecycle import advisory_xact_lock
from achilles.knowledge_store.constants import CODE_RUN_ALREADY_ACTIVE, CurationState
from achilles.knowledge_store.models import CurationRun


async def start_run(session: AsyncSession, *, trigger: str) -> int:
    """Insert a queued run; an unfinished one already holding the lock → 409."""
    run = CurationRun(trigger=trigger)
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409,
            CODE_RUN_ALREADY_ACTIVE,
            "Curation run already active",
            "A curation run is already in progress — wait for it to finish.",
        ) from exc
    return run.id


async def active_run(session: AsyncSession) -> CurationRun | None:
    """The one unfinished platform run, if any (the partial UNIQUE allows at most one)."""
    return await session.scalar(
        sa.select(CurationRun).where(
            CurationRun.state.in_([str(CurationState.QUEUED), str(CurationState.RUNNING)])
        )
    )


async def last_finished(session: AsyncSession) -> CurationRun | None:
    return await session.scalar(
        sa.select(CurationRun)
        .where(CurationRun.finished_at.is_not(None))
        .order_by(CurationRun.id.desc())
        .limit(1)
    )


async def cancel(session: AsyncSession, run_id: int) -> bool:
    """API-side cancel; a running worker's finish() then loses its transition (rowcount 0)."""
    return await finish(session, run_id, state=str(CurationState.CANCELLED))


async def mark_running(session: AsyncSession, run_id: int) -> bool:
    """False → the run is no longer queued (reaped or cancelled); don't proceed."""
    now = datetime.now(UTC)
    result = await session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id, CurationRun.state == str(CurationState.QUEUED))
        .values(state=str(CurationState.RUNNING), started_at=now, heartbeat_at=now)
    )
    return bool(getattr(result, "rowcount", 0))


async def heartbeat(session: AsyncSession, run_id: int) -> None:
    await session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id)
        .values(heartbeat_at=datetime.now(UTC))
    )


async def open_destructive_window(session: AsyncSession, run_id: int) -> bool:
    """Claim the destructive window unless a sync run is live (merge yields).

    sync_runs is referenced by table name, not model — the model lives across
    the module boundary in harvester (KS must not import back).
    """
    await advisory_xact_lock(session)
    result = await session.execute(
        sa.update(CurationRun)
        .where(
            CurationRun.id == run_id,
            CurationRun.state == str(CurationState.RUNNING),
            sa.text("NOT EXISTS (SELECT 1 FROM sync_runs WHERE state = 'running')"),
        )
        .values(destructive_since=datetime.now(UTC))
    )
    return bool(getattr(result, "rowcount", 0))


async def close_destructive_window(session: AsyncSession, run_id: int) -> None:
    await session.execute(
        sa.update(CurationRun).where(CurationRun.id == run_id).values(destructive_since=None)
    )


async def finish(
    session: AsyncSession,
    run_id: int,
    *,
    state: str,
    steps: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    """Terminal transition from an active state only.

    A reaped run stays failed — the reaper already freed the lock; overwriting
    would falsify the journal under a possibly newer active run.
    """
    active = [str(CurationState.QUEUED), str(CurationState.RUNNING)]
    result = await session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id, CurationRun.state.in_(active))
        .values(state=state, finished_at=datetime.now(UTC), steps=steps, error=error)
    )
    return bool(getattr(result, "rowcount", 0))
