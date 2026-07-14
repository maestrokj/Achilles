"""S3 backup storage against MinIO: roundtrip, multipart, rotation, creds, failures.

The MinIO container is module-scoped and lazy — only these tests pay for it.
"""

import hashlib
import json
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.minio import MinioContainer

from achilles.api.problems import ApiError
from achilles.knowledge_store.constants import BackupState
from achilles.knowledge_store.models import BackupSnapshot
from achilles.knowledge_store.services import backups
from achilles.knowledge_store.services.backup_storage import (
    S3BackupStorage,
    S3Credentials,
    resolve_storage,
)

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BUCKET = "achilles-backups"


@pytest.fixture(scope="module")
def minio_creds() -> Generator[S3Credentials]:
    with MinioContainer() as container:
        client = container.get_client()
        client.make_bucket(BUCKET)
        host_port = container.get_config()["endpoint"].replace("localhost", "127.0.0.1")
        yield S3Credentials(
            access_key=container.access_key,
            secret_key=container.secret_key,
            endpoint_url=f"http://{host_port}",
            region="us-east-1",
        )


@pytest.fixture
def storage(minio_creds: S3Credentials) -> S3BackupStorage:
    resolved = resolve_storage(f"s3://{BUCKET}/snapshots", creds_json=minio_creds.model_dump_json())
    assert isinstance(resolved, S3BackupStorage)
    return resolved


async def test_roundtrip_store_fetch_delete(storage: S3BackupStorage, tmp_path: Path):
    dump = tmp_path / "dump"
    dump.write_bytes(b"snapshot-bytes")

    location = await storage.store(dump, "snap.dump")
    assert location == f"s3://{BUCKET}/snapshots/snap.dump"

    fetched = await storage.fetch(location)
    assert fetched.read_bytes() == b"snapshot-bytes"

    await storage.delete(location)
    with pytest.raises(OSError, match=r"404|Not Found|NoSuchKey"):
        await storage.fetch(location)


async def test_multipart_sized_dump_survives_intact(storage: S3BackupStorage, tmp_path: Path):
    """~11 MB crosses boto's default 8 MB multipart threshold — bytes must match."""
    payload = b"pg-dump-block-" * 800_000  # ~11 MB
    dump = tmp_path / "big.dump"
    dump.write_bytes(payload)

    location = await storage.store(dump, "big.dump")
    fetched = await storage.fetch(location)
    assert hashlib.sha256(fetched.read_bytes()).digest() == hashlib.sha256(payload).digest()
    await storage.delete(location)


async def test_retention_rotates_remote_objects(
    storage: S3BackupStorage, db_session: AsyncSession, tmp_path: Path
):
    locations: list[str] = []
    for n in range(3):
        dump = tmp_path / f"dump{n}"
        dump.write_bytes(b"snapshot-bytes")
        location = await storage.store(dump, f"rotate-{n}.dump")
        locations.append(location)
        snapshot_id = await backups.start_snapshot(db_session)
        await backups.finish_snapshot(
            db_session, snapshot_id, state=str(BackupState.SUCCEEDED), location=location
        )
        await db_session.commit()

    removed = await backups.rotate_retention(db_session, storage, keep=1)
    await db_session.commit()

    assert removed == 2
    remaining = (await db_session.execute(sa.select(BackupSnapshot.location))).scalars().all()
    assert remaining == [locations[-1]]
    assert (await storage.fetch(locations[-1])).read_bytes() == b"snapshot-bytes"
    with pytest.raises(OSError):  # the rotated object is gone remotely too
        await storage.fetch(locations[0])


async def test_wrong_credentials_surface_as_os_error(minio_creds: S3Credentials, tmp_path: Path):
    """run_backup catches OSError → the snapshot journal ends up failed, not stuck."""
    bad = minio_creds.model_copy(update={"secret_key": "wrong-secret"})
    storage = S3BackupStorage(BUCKET, "snapshots", bad)
    dump = tmp_path / "dump"
    dump.write_bytes(b"snapshot-bytes")

    with pytest.raises(OSError):
        await storage.store(dump, "denied.dump")


def test_resolve_storage_s3_contract():
    resolved = resolve_storage(
        "s3://bucket/prefix",
        creds_json=json.dumps({"access_key": "a", "secret_key": "s"}),
    )
    assert isinstance(resolved, S3BackupStorage)
    # Ambient IAM: no creds is a valid configuration.
    assert isinstance(resolve_storage("s3://bucket"), S3BackupStorage)

    with pytest.raises(ApiError) as no_bucket:
        resolve_storage("s3://")
    assert no_bucket.value.status == 409

    with pytest.raises(ApiError) as bad_creds:
        resolve_storage("s3://bucket", creds_json="not-json")
    assert bad_creds.value.status == 409
