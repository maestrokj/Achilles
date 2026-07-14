"""sync_runs journal: per-source lock, lane gate, reaper, terminal transitions (P0)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.harvester.constants import SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import SyncRun
from achilles.harvester.services import sync_runs
from achilles.infra.lifecycle import reap_stale_runs
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import CurationRun
from achilles.knowledge_store.services import platform
from tests.factories.knowledge import create_source

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def _start(session: AsyncSession, source_id: int) -> int:
    run_id = await sync_runs.start_run(
        session,
        source_id=source_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.MANUAL),
    )
    await session.commit()
    return run_id


async def test_second_start_on_same_source_is_409(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    await _start(db_session, source.id)

    with pytest.raises(ApiError) as exc_info:
        await sync_runs.start_run(
            db_session,
            source_id=source.id,
            mode=str(SyncMode.FULL),
            trigger=str(SyncTrigger.MANUAL),
        )
    assert exc_info.value.status == 409
    await db_session.rollback()


async def test_lock_is_per_source(db_session: AsyncSession) -> None:
    first = await create_source(db_session)
    second = await create_source(db_session)

    await _start(db_session, first.id)
    run_id = await _start(db_session, second.id)  # no 409 — different source
    assert run_id


async def test_mark_running_transitions_once(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)

    assert await sync_runs.mark_running(db_session, run_id) is True
    await db_session.commit()
    assert await sync_runs.mark_running(db_session, run_id) is False  # no longer queued
    await db_session.rollback()


async def test_mark_running_yields_to_destructive_window(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)
    now = datetime.now(UTC)
    db_session.add(
        CurationRun(
            trigger=str(CurationTrigger.MANUAL),
            state=str(CurationState.RUNNING),
            started_at=now,
            heartbeat_at=now,
            destructive_since=now,
        )
    )
    await db_session.commit()

    assert await sync_runs.mark_running(db_session, run_id) is False
    await db_session.rollback()
    assert await sync_runs.get_state(db_session, run_id) == str(SyncState.QUEUED)


async def test_gate_ignores_window_without_destructive_flag(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)
    now = datetime.now(UTC)
    db_session.add(
        CurationRun(
            trigger=str(CurationTrigger.MANUAL),
            state=str(CurationState.RUNNING),
            started_at=now,
            heartbeat_at=now,
        )
    )
    await db_session.commit()

    assert await sync_runs.mark_running(db_session, run_id) is True
    await db_session.rollback()


async def test_gate_self_clears_on_zombie_curation_heartbeat(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)
    stale = datetime.now(UTC) - timedelta(minutes=10)
    db_session.add(
        CurationRun(
            trigger=str(CurationTrigger.MANUAL),
            state=str(CurationState.RUNNING),
            started_at=stale,
            heartbeat_at=stale,
            destructive_since=stale,
        )
    )
    await db_session.commit()

    assert await sync_runs.mark_running(db_session, run_id) is True
    await db_session.rollback()


async def test_reaper_fails_zombie_sync_run(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    source_id = source.id
    run_id = await _start(db_session, source_id)
    assert await sync_runs.mark_running(db_session, run_id)
    stale = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.execute(
        sa.update(SyncRun).where(SyncRun.id == run_id).values(heartbeat_at=stale)
    )
    await db_session.commit()

    swept = await reap_stale_runs(db_session)
    await db_session.commit()

    assert swept >= 1
    db_session.expire_all()
    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.FAILED)
    assert run.error_detail == "heartbeat lost"
    # Reaped = terminal: finished_at stamped, or the watchdog cadence would
    # read the source as never-synced and escalate to a spurious full sync.
    assert run.finished_at is not None
    # The lock is free again: a new run starts without a 409.
    assert await _start(db_session, source_id)


async def test_finish_is_terminal_only_from_active(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)
    assert await sync_runs.cancel(db_session, run_id) is True
    await db_session.commit()

    # A second terminal write must not falsify the journal.
    assert await sync_runs.finish(db_session, run_id, state=str(SyncState.SUCCEEDED)) is False
    await db_session.rollback()
    assert await sync_runs.get_state(db_session, run_id) == str(SyncState.CANCELLED)


async def test_heartbeat_skips_terminal_states(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    run_id = await _start(db_session, source.id)
    await sync_runs.cancel(db_session, run_id)
    await db_session.commit()

    await sync_runs.heartbeat(db_session, run_id)
    await db_session.commit()
    db_session.expire_all()
    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.heartbeat_at is None


async def test_platform_settings_seeded(db_session: AsyncSession) -> None:
    row = await platform.get_platform_settings(db_session)
    assert row.timezone == "UTC"
    assert row.sync_interval_minutes == 15
    assert row.reconcile_minute_of_week == 8820
