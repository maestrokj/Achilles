"""Scheduler tick bodies: journal row + publish exactly once, silent losers (P1)."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import sqlalchemy as sa
from redis.asyncio import Redis
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import Settings
from achilles.harvester import jobs
from achilles.harvester.constants import SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import SyncRun
from achilles.knowledge_store import jobs as ks_jobs
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import CurationRun
from tests.factories.admin import set_platform_settings
from tests.factories.knowledge import create_source

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", {})

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def ticks_use_test_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)
    monkeypatch.setattr(ks_jobs, "app_settings", test_settings)


async def _finished_run(
    session: AsyncSession,
    source_id: int,
    *,
    mode: SyncMode = SyncMode.INCREMENTAL,
    state: SyncState = SyncState.SUCCEEDED,
    finished_ago: timedelta,
) -> None:
    moment = NOW - finished_ago
    session.add(
        SyncRun(
            source_id=source_id,
            mode=str(mode),
            trigger=str(SyncTrigger.SCHEDULE),
            state=str(state),
            started_at=moment,
            finished_at=moment,
        )
    )
    await session.commit()


async def test_sync_tick_publishes_for_overdue_sources_only(
    db_session: AsyncSession, redis_durable: Redis
) -> None:
    overdue = await create_source(db_session)
    overdue_id = overdue.id
    fresh = await create_source(db_session)
    fresh_id = fresh.id
    paused = await create_source(db_session, state="paused")
    paused_id = paused.id
    await _finished_run(db_session, overdue_id, finished_ago=timedelta(minutes=30))
    await _finished_run(db_session, fresh_id, finished_ago=timedelta(minutes=2))
    await _finished_run(db_session, paused_id, finished_ago=timedelta(hours=3))

    await jobs.sync_tick(CTX)

    runs = (
        await db_session.execute(
            sa.select(SyncRun.source_id, SyncRun.mode, SyncRun.trigger).where(
                SyncRun.state == str(SyncState.QUEUED)
            )
        )
    ).all()
    assert runs == [(overdue_id, str(SyncMode.INCREMENTAL), str(SyncTrigger.SCHEDULE))]
    run_id = await db_session.scalar(
        sa.select(SyncRun.id).where(SyncRun.state == str(SyncState.QUEUED))
    )
    assert await redis_durable.exists(f"dedup:job:sync:{run_id}")

    # A second tick within the interval publishes nothing new (the lock holds).
    await jobs.sync_tick(CTX)
    queued = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(SyncRun)
        .where(SyncRun.state == str(SyncState.QUEUED))
    )
    assert queued == 1


async def test_sync_tick_pauses_under_org_maintenance(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    source_id = source.id
    await _finished_run(db_session, source_id, finished_ago=timedelta(hours=3))
    await set_platform_settings(db_session, maintenance_mode=True)

    await jobs.sync_tick(CTX)

    queued = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(SyncRun)
        .where(SyncRun.state == str(SyncState.QUEUED))
    )
    assert queued == 0, "org maintenance pauses scheduled launches"


async def test_sync_tick_escalates_to_watchdog_on_silence(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    source_id = source.id
    # Failed runs keep coming, the last success is beyond the silence threshold.
    await _finished_run(
        db_session, source_id, state=SyncState.SUCCEEDED, finished_ago=timedelta(hours=20)
    )
    await _finished_run(
        db_session, source_id, state=SyncState.FAILED, finished_ago=timedelta(minutes=30)
    )

    await jobs.sync_tick(CTX)

    run = await db_session.scalar(sa.select(SyncRun).where(SyncRun.state == str(SyncState.QUEUED)))
    assert run is not None
    assert run.trigger == str(SyncTrigger.WATCHDOG)


async def test_reconcile_tick_fires_for_uncovered_sources_only(db_session: AsyncSession) -> None:
    # A never-reconciled source is overdue → fires; one whose window is already
    # covered by a recent reconciliation run stays quiet (window math is unit-
    # tested — here we prove the tick reads the anchor and starts exactly one).
    uncovered = await create_source(db_session)
    uncovered_id = uncovered.id
    covered = await create_source(db_session)
    covered_id = covered.id
    await _finished_run(
        db_session, covered_id, mode=SyncMode.RECONCILIATION, finished_ago=timedelta(0)
    )

    await jobs.reconcile_tick(CTX)

    runs = (
        await db_session.execute(
            sa.select(SyncRun.source_id, SyncRun.mode).where(SyncRun.state == str(SyncState.QUEUED))
        )
    ).all()
    assert runs == [(uncovered_id, str(SyncMode.RECONCILIATION))]
    del covered_id


async def test_curation_tick_respects_cadence(db_session: AsyncSession) -> None:
    # No prior runs → due immediately.
    await ks_jobs.curation_tick(CTX)
    run = await db_session.scalar(sa.select(CurationRun))
    assert run is not None
    assert run.trigger == str(CurationTrigger.SCHEDULE)

    # Within the cadence (and with the lock held) nothing new appears.
    await ks_jobs.curation_tick(CTX)
    count = await db_session.scalar(sa.select(sa.func.count()).select_from(CurationRun))
    assert count == 1


async def test_curation_tick_ignores_model_change_runs(db_session: AsyncSession) -> None:
    # A fresh re-embed run does no grooming — it must not reset the cadence anchor.
    db_session.add(
        CurationRun(
            trigger=str(CurationTrigger.MODEL_CHANGE),
            state=str(CurationState.SUCCEEDED),
            finished_at=NOW,
        )
    )
    await db_session.commit()

    await ks_jobs.curation_tick(CTX)

    scheduled = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(CurationRun)
        .where(CurationRun.trigger == str(CurationTrigger.SCHEDULE))
    )
    assert scheduled == 1


async def test_health_tick_publishes_probe_jobs(
    db_session: AsyncSession, redis_durable: Redis
) -> None:
    due = await create_source(db_session)  # last_probe_at NULL → due
    due_id = due.id
    fresh = await create_source(db_session, last_probe_at=NOW - timedelta(hours=1))
    del fresh

    await jobs.health_tick(CTX)

    stamp = f"{datetime.now(UTC):%Y%m%d}"
    assert await redis_durable.exists(f"dedup:job:probe:{due_id}:{stamp}")
    keys = [key async for key in redis_durable.scan_iter(match="dedup:job:probe:*")]
    assert len(keys) == 1
