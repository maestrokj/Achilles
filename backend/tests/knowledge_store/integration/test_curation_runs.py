"""curation_runs journal: lifecycle, single-active lock, zombie reaping (P1)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.infra import lifecycle
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import CurationRun
from achilles.knowledge_store.services import curation

pytestmark = [pytest.mark.integration, pytest.mark.p1]

MANUAL = CurationTrigger.MANUAL.value


async def test_run_lifecycle_lands_in_the_journal(db_session: AsyncSession):
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await db_session.commit()

    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert (run.state, run.trigger) == (CurationState.QUEUED.value, MANUAL)
    assert run.started_at is None

    await curation.mark_running(db_session, run_id)
    await curation.finish(
        db_session,
        run_id,
        state=CurationState.SUCCEEDED.value,
        steps={"edges_materialized": 7},
    )
    await db_session.commit()
    db_session.expire_all()

    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.SUCCEEDED.value
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.steps == {"edges_materialized": 7}


async def test_second_active_run_gets_409(db_session: AsyncSession):
    await curation.start_run(db_session, trigger=MANUAL)
    await db_session.commit()

    with pytest.raises(ApiError) as exc_info:
        await curation.start_run(db_session, trigger=MANUAL)
    assert exc_info.value.status == 409
    await db_session.rollback()


@pytest.mark.parametrize(
    "terminal",
    [CurationState.SUCCEEDED.value, CurationState.FAILED.value, CurationState.CANCELLED.value],
)
async def test_terminal_state_frees_the_lock(db_session: AsyncSession, terminal: str):
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await curation.finish(db_session, run_id, state=terminal)
    await db_session.commit()

    next_id = await curation.start_run(db_session, trigger=MANUAL)
    await db_session.commit()
    assert next_id != run_id


async def test_zombie_run_is_reaped_to_failed_and_frees_the_lock(db_session: AsyncSession):
    """Three missed heartbeats (~90s) → the reaper terminates the run (not `stale`)."""
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await curation.mark_running(db_session, run_id)
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id)
        .values(heartbeat_at=now - timedelta(seconds=120))
    )
    await db_session.commit()

    swept = await lifecycle.reap_stale_runs(db_session, now=now)  # the real registry
    await db_session.commit()
    db_session.expire_all()

    assert swept == 1
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.FAILED.value
    assert run.error == "heartbeat lost"

    await curation.start_run(db_session, trigger=MANUAL)  # the lock is free again
    await db_session.commit()


async def test_queued_zombie_is_reaped_after_a_lost_publish(db_session: AsyncSession):
    """A queued row whose job publish was lost ages via created_at and frees the lock."""
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await db_session.commit()
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id)
        .values(created_at=now - lifecycle.QUEUED_ZOMBIE_AFTER - timedelta(seconds=30))
    )
    await db_session.commit()

    swept = await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()
    db_session.expire_all()

    assert swept == 1
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.FAILED.value

    await curation.start_run(db_session, trigger=MANUAL)  # the lock is free again
    await db_session.commit()


async def test_reaped_run_is_not_resurrected_by_a_late_job(db_session: AsyncSession):
    """mark_running/finish respect the reaper's verdict — the journal is not falsified."""
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await curation.mark_running(db_session, run_id)
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(CurationRun)
        .where(CurationRun.id == run_id)
        .values(heartbeat_at=now - timedelta(seconds=120))
    )
    await db_session.commit()
    await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()

    finished = await curation.finish(db_session, run_id, state=CurationState.SUCCEEDED.value)
    started = await curation.mark_running(db_session, run_id)
    await db_session.commit()
    db_session.expire_all()

    assert (finished, started) == (False, False)
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.FAILED.value


async def test_live_heartbeat_survives_the_reaper(db_session: AsyncSession):
    run_id = await curation.start_run(db_session, trigger=MANUAL)
    await curation.mark_running(db_session, run_id)
    await curation.heartbeat(db_session, run_id)
    await db_session.commit()

    swept = await lifecycle.reap_stale_runs(db_session)
    assert swept == 0


async def test_trigger_and_state_checks(db_session: AsyncSession):
    with pytest.raises(IntegrityError):
        db_session.add(CurationRun(trigger="cosmic_ray"))
        await db_session.flush()
    await db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.add(CurationRun(trigger=MANUAL, state="paused"))
        await db_session.flush()
    await db_session.rollback()
