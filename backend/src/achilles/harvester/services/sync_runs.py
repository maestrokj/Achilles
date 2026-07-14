"""sync_runs journal ops (harvester/data-model.html#sync-runs-table).

One active run per source — the correctness lock is the partial UNIQUE next to
the journal, never Redis. mark_running additionally yields to an open curation
destructive window (lane coordination, knowledge-store/lifecycle.html#coordination):
both transition transactions serialize on one advisory lock, killing the
write-skew of the two facing NOT EXISTS gates.
"""

from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.harvester.constants import SyncState
from achilles.harvester.models import SyncRun
from achilles.infra.lifecycle import RUN_ZOMBIE_AFTER, advisory_xact_lock
from achilles.knowledge_store.constants import CODE_RUN_ALREADY_ACTIVE, CurationState
from achilles.knowledge_store.models import CurationRun

_ACTIVE = [str(SyncState.QUEUED), str(SyncState.RUNNING)]


class _Unset:
    """Sentinel: 'leave the column unchanged' for optional journal fields."""


UNSET: Final = _Unset()


async def start_run(
    session: AsyncSession,
    *,
    source_id: int,
    mode: str,
    trigger: str,
    scope: dict[str, Any] | None = None,
) -> int:
    """Insert a queued run; an unfinished one already holding the source lock → 409."""
    run = SyncRun(source_id=source_id, mode=mode, trigger=trigger, scope=scope)
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409,
            CODE_RUN_ALREADY_ACTIVE,
            "Sync run already active",
            "A sync of this source is already in progress — wait for it to finish or cancel it.",
        ) from exc
    return run.id


def _destructive_window_open(now: datetime) -> sa.ColumnElement[bool]:
    """A live running curation run with an open destructive window (merge/retention)."""
    return (
        sa.select(CurationRun.id)
        .where(
            CurationRun.state == str(CurationState.RUNNING),
            CurationRun.destructive_since.is_not(None),
            # The heartbeat predicate self-clears a zombie window; the reaper
            # finishes the job on the next sweep.
            CurationRun.heartbeat_at > now - RUN_ZOMBIE_AFTER,
        )
        .exists()
    )


async def mark_running(session: AsyncSession, run_id: int) -> bool:
    """Queued → running, unless the curation destructive window is open.

    False → either the gate is closed (retry later) or the run is no longer
    queued (cancelled/reaped) — the caller distinguishes via get_state.
    """
    await advisory_xact_lock(session)
    now = datetime.now(UTC)
    result = await session.execute(
        sa.update(SyncRun)
        .where(
            SyncRun.id == run_id,
            SyncRun.state == str(SyncState.QUEUED),
            ~_destructive_window_open(now),
        )
        .values(state=str(SyncState.RUNNING), started_at=now, heartbeat_at=now)
    )
    return bool(getattr(result, "rowcount", 0))


async def heartbeat(session: AsyncSession, run_id: int) -> None:
    """Beat while active only — queued beats keep a gate-blocked run off the reaper."""
    await session.execute(
        sa.update(SyncRun)
        .where(SyncRun.id == run_id, SyncRun.state.in_(_ACTIVE))
        .values(heartbeat_at=datetime.now(UTC))
    )


async def get_state(session: AsyncSession, run_id: int) -> str | None:
    return await session.scalar(sa.select(SyncRun.state).where(SyncRun.id == run_id))


async def update_progress(
    session: AsyncSession,
    run_id: int,
    *,
    entities_done: int,
    entities_total: int | None = None,
    checkpoint: dict[str, Any] | None | _Unset = UNSET,
    error_count: int | None = None,
) -> None:
    values: dict[str, Any] = {"entities_done": entities_done}
    if not isinstance(checkpoint, _Unset):
        values["checkpoint"] = checkpoint
    if entities_total is not None:
        values["entities_total"] = entities_total
    if error_count is not None:
        values["error_count"] = error_count
    await session.execute(sa.update(SyncRun).where(SyncRun.id == run_id).values(**values))


async def finish(
    session: AsyncSession,
    run_id: int,
    *,
    state: str,
    error_detail: str | None = None,
) -> bool:
    """Terminal transition from an active state only.

    A reaped run stays failed — the reaper already freed the lock; overwriting
    would falsify the journal under a possibly newer active run.
    """
    result = await session.execute(
        sa.update(SyncRun)
        .where(SyncRun.id == run_id, SyncRun.state.in_(_ACTIVE))
        .values(state=state, finished_at=datetime.now(UTC), error_detail=error_detail)
    )
    return bool(getattr(result, "rowcount", 0))


async def cancel(session: AsyncSession, run_id: int) -> bool:
    """API-side cancel; a running worker notices at the next page boundary."""
    return await finish(session, run_id, state=str(SyncState.CANCELLED))


async def active_run(session: AsyncSession, source_id: int) -> SyncRun | None:
    return await session.scalar(
        sa.select(SyncRun).where(SyncRun.source_id == source_id, SyncRun.state.in_(_ACTIVE))
    )


async def last_finished(session: AsyncSession, source_id: int) -> SyncRun | None:
    return await session.scalar(
        sa.select(SyncRun)
        .where(SyncRun.source_id == source_id, SyncRun.state.not_in(_ACTIVE))
        .order_by(SyncRun.id.desc())
        .limit(1)
    )


async def list_runs(session: AsyncSession, source_id: int, *, limit: int = 50) -> list[SyncRun]:
    result = await session.scalars(
        sa.select(SyncRun)
        .where(SyncRun.source_id == source_id)
        .order_by(SyncRun.id.desc())
        .limit(limit)
    )
    return list(result)
