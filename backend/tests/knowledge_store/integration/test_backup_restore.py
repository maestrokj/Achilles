"""Backups: journal + lock + rotation always; dump/restore roundtrip needs pg_dump (P1).

macOS: `brew install libpq` and put its bin on PATH — the pg-tool tests skip
loudly otherwise. In the images the worker carries postgresql-client.
"""

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tests.factories.knowledge import acl_scene, create_chunk, create_entity, grant
from tests.factories.users import create_user

from achilles.api.problems import ApiError
from achilles.config import Settings
from achilles.infra import lifecycle
from achilles.knowledge_store.constants import AclScope, BackupState
from achilles.knowledge_store.models import BackupSettings, BackupSnapshot
from achilles.knowledge_store.retrieval import lexical
from achilles.knowledge_store.services import backups
from achilles.knowledge_store.services.backup_storage import FileBackupStorage, resolve_storage

pytestmark = [pytest.mark.integration, pytest.mark.p1]

needs_pg_tools = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="pg_dump/pg_restore not on PATH (macOS: brew install libpq + PATH)",
)


async def test_second_active_snapshot_hits_the_lock(db_session: AsyncSession):
    await backups.start_snapshot(db_session)
    await db_session.commit()

    with pytest.raises(ApiError) as exc_info:
        await backups.start_snapshot(db_session)
    assert exc_info.value.status == 409
    await db_session.rollback()


async def test_snapshot_journal_lifecycle(db_session: AsyncSession):
    snapshot_id = await backups.start_snapshot(db_session)
    await backups.finish_snapshot(
        db_session,
        snapshot_id,
        state=BackupState.SUCCEEDED.value,
        size_bytes=1024,
        location="file:///backups/x.dump",
    )
    await db_session.commit()
    db_session.expire_all()

    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.SUCCEEDED.value
    assert snapshot.size_bytes == 1024
    assert snapshot.finished_at is not None


async def test_zombie_snapshot_is_reaped_and_frees_the_lock(db_session: AsyncSession):
    snapshot_id = await backups.start_snapshot(db_session)
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(BackupSnapshot)
        .where(BackupSnapshot.id == snapshot_id)
        .values(heartbeat_at=now - timedelta(seconds=120))
    )
    await db_session.commit()

    swept = await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()
    db_session.expire_all()

    assert swept == 1
    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.FAILED.value
    assert snapshot.error == "heartbeat lost"

    await backups.start_snapshot(db_session)  # the schedule is unblocked
    await db_session.commit()


async def test_snapshot_dead_before_first_beat_is_reaped(db_session: AsyncSession):
    """Lost publish / worker killed before the first heartbeat: the staleness
    anchor falls back to started_at, the lock must not be held forever."""
    snapshot_id = await backups.start_snapshot(db_session)  # heartbeat_at is NULL
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(BackupSnapshot)
        .where(BackupSnapshot.id == snapshot_id)
        .values(started_at=now - timedelta(seconds=120))
    )
    await db_session.commit()

    swept = await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()
    db_session.expire_all()

    assert swept == 1
    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.FAILED.value

    await backups.start_snapshot(db_session)  # the schedule is unblocked
    await db_session.commit()


async def test_reaped_snapshot_is_not_resurrected_by_a_late_job(db_session: AsyncSession):
    snapshot_id = await backups.start_snapshot(db_session)
    now = datetime.now(UTC)
    await db_session.execute(
        sa.update(BackupSnapshot)
        .where(BackupSnapshot.id == snapshot_id)
        .values(started_at=now - timedelta(seconds=120))
    )
    await db_session.commit()
    await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()

    finished = await backups.finish_snapshot(
        db_session, snapshot_id, state=BackupState.SUCCEEDED.value, location="file:///late.dump"
    )
    await db_session.commit()
    db_session.expire_all()

    assert finished is False
    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.FAILED.value  # the reaper's verdict stands


async def test_storage_roundtrip_survives_special_chars_in_the_path(tmp_path: Path):
    """as_uri() percent-encodes; fetch/delete must decode back (space, Cyrillic)."""
    root = tmp_path / "back up архив"
    storage = resolve_storage(root.as_uri())
    dump = tmp_path / "dump"
    dump.write_bytes(b"snapshot-bytes")

    location = await storage.store(dump, "snap.dump")
    fetched = await storage.fetch(location)
    assert await asyncio.to_thread(fetched.exists)
    assert fetched == root / "snap.dump"  # decoded, not a literal %20 path

    await storage.delete(location)
    assert not await asyncio.to_thread(fetched.exists)


async def test_retention_rotates_journal_and_storage(db_session: AsyncSession, tmp_path: Path):
    storage = FileBackupStorage(tmp_path / "backups")
    locations: list[str] = []
    for n in range(3):
        dump = tmp_path / f"dump{n}"
        dump.write_bytes(b"snapshot-bytes")
        location = await storage.store(dump, f"snap-{n}.dump")
        locations.append(location)
        snapshot_id = await backups.start_snapshot(db_session)
        await backups.finish_snapshot(
            db_session, snapshot_id, state=BackupState.SUCCEEDED.value, location=location
        )
        await db_session.commit()

    removed = await backups.rotate_retention(db_session, storage, keep=1)
    await db_session.commit()

    assert removed == 2
    remaining = (await db_session.execute(sa.select(BackupSnapshot.location))).scalars().all()
    assert remaining == [locations[-1]]  # newest survives
    kept_files = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert kept_files == ["snap-2.dump"]


async def test_settings_singleton_is_seeded_and_unique(db_session: AsyncSession):
    settings_row = await backups.get_settings(db_session)
    assert (settings_row.frequency, settings_row.retention_count) == ("daily", 14)
    assert settings_row.destination_url is None

    with pytest.raises(IntegrityError):  # CHECK (id = 1): the row is one by construction
        db_session.add(BackupSettings(id=2))
        await db_session.flush()
    await db_session.rollback()


def test_unconfigured_or_unsupported_destination_is_409():
    with pytest.raises(ApiError) as exc_info:
        resolve_storage(None)
    assert exc_info.value.status == 409

    with pytest.raises(ApiError):  # gs:// is not a supported scheme
        resolve_storage("gs://bucket/prefix")


@needs_pg_tools
async def test_dump_restore_roundtrip_returns_everything(
    db_engine: AsyncEngine, test_settings: Settings, tmp_path: Path
):
    """Body, ACL and the FTS projection survive a full dump → wipe → restore cycle."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        user = await create_user(session)
        scene = await acl_scene(session, user=user)
        entity = await create_entity(session, source_id=scene.source.id)
        await create_chunk(session, entity_id=entity.id, text="phoenix rises from the dump")
        await grant(
            session,
            entity_id=entity.id,
            scope=AclScope.GROUP.value,
            source_group_id=scene.group.id,
        )
        user_id, entity_id = user.id, entity.id

    dsn = backups.libpq_dsn(test_settings.database_url)
    dump_path = tmp_path / "roundtrip.dump"
    await backups.dump_database(dsn, dump_path)
    assert dump_path.stat().st_size > 0

    async with factory() as session:  # wipe: the snapshot must bring this back
        await session.execute(sa.text("TRUNCATE entities RESTART IDENTITY CASCADE"))
        await session.commit()

    await db_engine.dispose()  # pg_restore --clean needs no live locks from us
    await backups.restore_database(dsn, dump_path)

    async with factory() as session:
        hits = await lexical.search(session, user_id=user_id, query="phoenix", top_k=10)
        assert [h.entity_id for h in hits] == [entity_id]  # FTS + ACL restored
