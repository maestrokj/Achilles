"""Admin knowledge ops: 202 + journal row + published job, 409 single-flight (API)."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import create_chunk, create_entity, create_source
from tests.factories.users import create_user
from tests.knowledge_store.conftest import configure_backup_destination

from achilles.auth.constants import UserRole
from achilles.knowledge_store.constants import BackupState, CurationState
from achilles.knowledge_store.models import BackupSnapshot, CurationRun
from achilles.knowledge_store.services.maintenance import MAINTENANCE_KEY

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/knowledge"


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


async def test_reindex_journals_queued_and_publishes(
    client: AsyncClient, db_session: AsyncSession, redis_durable: Redis, as_admin: None
):
    resp = await client.post(f"{BASE}/reindex")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == CurationState.QUEUED.value
    assert await redis_durable.exists(f"dedup:job:curation:{run_id}")  # published to the lane


async def test_duplicate_reindex_is_409(client: AsyncClient, as_admin: None):
    assert (await client.post(f"{BASE}/reindex")).status_code == 202
    duplicate = await client.post(f"{BASE}/reindex")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "RUN_ALREADY_ACTIVE"


async def test_backup_without_destination_is_409(client: AsyncClient, as_admin: None):
    resp = await client.post(f"{BASE}/backup")
    assert resp.status_code == 409
    assert resp.json()["code"] == "BACKUP_NOT_CONFIGURED"


async def test_backup_journals_running_and_publishes(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_durable: Redis,
    tmp_path: Path,
    as_admin: None,
):
    await configure_backup_destination(db_session, tmp_path)

    resp = await client.post(f"{BASE}/backup")
    assert resp.status_code == 202
    snapshot_id = resp.json()["snapshot_id"]

    snapshot = await db_session.get(BackupSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.state == BackupState.RUNNING.value
    assert await redis_durable.exists(f"dedup:job:backup:manual:{snapshot_id}")

    duplicate = await client.post(f"{BASE}/backup")
    assert duplicate.status_code == 409  # single-flight lock on the journal


async def test_restore_unknown_snapshot_is_404(client: AsyncClient, as_admin: None):
    resp = await client.post(f"{BASE}/restore", json={"snapshot_id": 4242})
    assert resp.status_code == 404


async def make_succeeded_snapshot(db_session: AsyncSession) -> int:
    db_session.add(
        BackupSnapshot(
            state=BackupState.SUCCEEDED.value,
            started_at=sa.func.now(),
            location="file:///backups/x.dump",
        )
    )
    await db_session.commit()
    return (await db_session.execute(sa.select(BackupSnapshot.id))).scalar_one()


async def test_restore_of_a_succeeded_snapshot_is_202(
    client: AsyncClient, db_session: AsyncSession, redis_durable: Redis, as_admin: None
):
    snapshot_id = await make_succeeded_snapshot(db_session)

    resp = await client.post(f"{BASE}/restore", json={"snapshot_id": snapshot_id})
    assert resp.status_code == 202
    keys = await redis_durable.keys(f"dedup:job:restore:{snapshot_id}:*")
    assert keys
    # The route claims the maintenance flag atomically before publishing…
    assert await redis_durable.exists(MAINTENANCE_KEY)
    # …and always with a TTL, so a dead worker cannot gate the platform forever.
    assert await redis_durable.ttl(MAINTENANCE_KEY) > 0


async def test_concurrent_restore_dies_on_the_claim(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    """The claim is the single-flight lock: two pg_restore must never overlap."""
    snapshot_id = await make_succeeded_snapshot(db_session)

    first = await client.post(f"{BASE}/restore", json={"snapshot_id": snapshot_id})
    second = await client.post(f"{BASE}/restore", json={"snapshot_id": snapshot_id})

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "MAINTENANCE"


async def test_sources_slice_carries_counters_and_emptiness(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    empty = await client.get(f"{BASE}/sources")
    assert empty.status_code == 200
    assert empty.json() == {"sources": [], "is_empty": True}

    source = await create_source(db_session)
    entity = await create_entity(db_session, source_id=source.id)
    await create_chunk(db_session, entity_id=entity.id, ordinal=0)
    await create_chunk(db_session, entity_id=entity.id, ordinal=1)

    resp = await client.get(f"{BASE}/sources")
    body = resp.json()
    assert body["is_empty"] is False
    (slice_,) = body["sources"]
    assert slice_["id"] == source.id
    assert slice_["connector_type"] == "jira"
    assert (slice_["entity_count"], slice_["chunk_count"]) == (1, 2)
    assert slice_["last_sync"] is None  # TODO(seam): Harvester, stage 5
