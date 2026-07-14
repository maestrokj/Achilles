"""backup_snapshots journal + pg_dump runtime (lifecycle.html#backup).

One Postgres → the dump is consistent by construction: body, graph and rights in
one point (vectors join in stage 4). One active backup per platform — the lock
is the partial UNIQUE on the journal; a colliding start is rejected, not queued.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt
from achilles.knowledge_store.constants import CODE_RUN_ALREADY_ACTIVE, BackupState
from achilles.knowledge_store.models import BackupSettings, BackupSnapshot
from achilles.knowledge_store.services.backup_storage import BackupStorage, not_configured

# Tail kept from tool stderr / failure detail — sized for the journal error column.
ERROR_TAIL = 2000


class BackupToolError(Exception):
    """pg_dump/pg_restore exited non-zero; the message carries the stderr tail."""


async def get_settings(session: AsyncSession) -> BackupSettings:
    settings = await session.get(BackupSettings, 1)
    if settings is None:  # pragma: no cover — the migration seeds the singleton
        msg = "backup_settings singleton row is missing"
        raise RuntimeError(msg)
    return settings


def decrypted_creds(settings: BackupSettings, *, key: bytes) -> str | None:
    """destination_creds_enc → plain JSON for resolve_storage; None = ambient IAM."""
    if settings.destination_creds_enc is None:
        return None
    try:
        return decrypt(settings.destination_creds_enc, key=key)
    except CiphertextInvalidError as exc:
        raise not_configured("The stored S3 credentials cannot be decrypted.") from exc


async def start_snapshot(session: AsyncSession) -> int:
    """Insert a running snapshot row; an active one holding the lock → 409."""
    snapshot = BackupSnapshot(started_at=datetime.now(UTC))
    session.add(snapshot)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(
            409,
            CODE_RUN_ALREADY_ACTIVE,
            "Backup already running",
            "A backup is already in progress — wait for it to finish.",
        ) from exc
    return snapshot.id


async def heartbeat_snapshot(session: AsyncSession, snapshot_id: int) -> None:
    await session.execute(
        sa.update(BackupSnapshot)
        .where(BackupSnapshot.id == snapshot_id)
        .values(heartbeat_at=datetime.now(UTC))
    )


async def finish_snapshot(
    session: AsyncSession,
    snapshot_id: int,
    *,
    state: str,
    size_bytes: int | None = None,
    location: str | None = None,
    error: str | None = None,
) -> bool:
    """Terminal transition from running only.

    A reaped snapshot stays failed — the reaper already freed the lock.
    """
    result = await session.execute(
        sa.update(BackupSnapshot)
        .where(BackupSnapshot.id == snapshot_id, BackupSnapshot.state == str(BackupState.RUNNING))
        .values(
            state=state,
            finished_at=datetime.now(UTC),
            size_bytes=size_bytes,
            location=location,
            error=error,
        )
    )
    return bool(getattr(result, "rowcount", 0))


async def rotate_retention(session: AsyncSession, storage: BackupStorage, *, keep: int) -> int:
    """Delete snapshots beyond `keep` newest from the journal AND the storage."""
    stale = (
        (
            await session.execute(
                sa.select(BackupSnapshot.id, BackupSnapshot.location)
                .where(BackupSnapshot.state == str(BackupState.SUCCEEDED))
                .order_by(BackupSnapshot.started_at.desc())
                .offset(keep)
            )
        )
        .tuples()
        .all()
    )
    for _, location in stale:
        if location:
            await storage.delete(location)
    if stale:
        await session.execute(
            sa.delete(BackupSnapshot).where(BackupSnapshot.id.in_(row[0] for row in stale))
        )
    return len(stale)


def libpq_dsn(database_url: str) -> str:
    """The app URL is SQLAlchemy-flavoured (postgresql+asyncpg://); libpq wants plain."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _run_tool(*argv: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        tail = stderr.decode()[-ERROR_TAIL:]
        raise BackupToolError(f"{argv[0]} exited {process.returncode}: {tail}")


async def dump_database(dsn: str, target: Path) -> None:
    await _run_tool("pg_dump", "--format=custom", "--file", str(target), "--dbname", dsn)


async def restore_database(dsn: str, dump_path: Path) -> None:
    await _run_tool(
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--dbname",
        dsn,
        str(dump_path),
    )
