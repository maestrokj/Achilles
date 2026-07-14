"""SAQ job bodies invoked directly: backup, restore, curation stub, cron tick (P1).

The jobs build their own connections from module-level settings — tests point
them at the containers via monkeypatch.
"""

import asyncio
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from redis.asyncio import Redis
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import create_chunk, create_entity, create_source
from tests.knowledge_store.conftest import configure_backup_destination

from achilles.config import Settings
from achilles.knowledge_store import jobs
from achilles.knowledge_store.constants import BackupState, CurationState, CurationTrigger
from achilles.knowledge_store.models import BackupSnapshot, CurationRun, Entity
from achilles.knowledge_store.services import backups, curation, maintenance
from achilles.knowledge_store.services.backup_storage import resolve_storage

pytestmark = [pytest.mark.integration, pytest.mark.p1]

needs_pg_tools = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="pg_dump/pg_restore not on PATH (macOS: brew install libpq + PATH)",
)

CTX = cast("Context", {})


@pytest.fixture(autouse=True)
def jobs_use_test_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


@needs_pg_tools
async def test_run_backup_dumps_and_journals(db_session: AsyncSession, tmp_path: Path) -> None:
    await configure_backup_destination(db_session, tmp_path)
    snapshot_id = await backups.start_snapshot(db_session)
    await db_session.commit()

    await jobs.run_backup(CTX, snapshot_id=snapshot_id)

    db_session.expire_all()
    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.SUCCEEDED.value
    assert snapshot.size_bytes and snapshot.size_bytes > 0
    assert snapshot.location is not None
    storage = resolve_storage((tmp_path / "backups").as_uri())
    dump_file = await storage.fetch(snapshot.location)
    assert await asyncio.to_thread(dump_file.exists)


async def test_run_backup_without_destination_marks_failed(db_session: AsyncSession) -> None:
    snapshot_id = await backups.start_snapshot(db_session)
    await db_session.commit()

    await jobs.run_backup(CTX, snapshot_id=snapshot_id)

    db_session.expire_all()
    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.FAILED.value
    assert snapshot.error


@needs_pg_tools
async def test_run_restore_roundtrip_under_maintenance(
    db_session: AsyncSession, redis_durable: Redis, tmp_path: Path
) -> None:
    await configure_backup_destination(db_session, tmp_path)
    source = await create_source(db_session)
    entity = await create_entity(db_session, source_id=source.id)
    await create_chunk(db_session, entity_id=entity.id, text="restore me")
    entity_id = entity.id

    snapshot_id = await backups.start_snapshot(db_session)
    await db_session.commit()
    await jobs.run_backup(CTX, snapshot_id=snapshot_id)

    await db_session.execute(sa.delete(Entity).where(Entity.id == entity_id))
    await db_session.commit()

    await jobs.run_restore(CTX, snapshot_id=snapshot_id)

    db_session.expire_all()
    assert await db_session.get(Entity, entity_id) is not None  # data is back
    assert await maintenance.is_maintenance(redis_durable) is False  # flag lifted in finally


async def test_run_curation_on_empty_db_succeeds_with_zero_stats(
    db_session: AsyncSession,
) -> None:
    run_id = await curation.start_run(db_session, trigger=CurationTrigger.MANUAL.value)
    await db_session.commit()

    await jobs.run_curation(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.SUCCEEDED.value
    assert run.steps == {
        "refs_materialized": 0,
        "duplicates_merged": 0,
        "entities_rescored": 0,
    }
    assert run.started_at is not None
    assert run.finished_at is not None


async def test_backup_tick_journals_and_publishes_when_due(
    db_session: AsyncSession, redis_durable: Redis, tmp_path: Path
) -> None:
    await configure_backup_destination(db_session, tmp_path)

    await jobs.backup_tick(CTX)

    snapshot = (await db_session.execute(sa.select(BackupSnapshot))).scalar_one()
    assert snapshot.state == BackupState.RUNNING.value
    keys = cast("list[Any]", await redis_durable.keys("dedup:job:backup:*"))
    assert keys  # published to the background lane exactly once

    await jobs.backup_tick(CTX)  # double tick dies on the journal lock, no second row
    count = await db_session.scalar(sa.select(sa.func.count(BackupSnapshot.id)))
    assert count == 1


async def test_backup_tick_skips_when_unconfigured(db_session: AsyncSession) -> None:
    await jobs.backup_tick(CTX)
    count = await db_session.scalar(sa.select(sa.func.count(BackupSnapshot.id)))
    assert count == 0
