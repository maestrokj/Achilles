"""SAQ jobs: curation pass, embedding refresh, backup, restore, backup cron tick.

Jobs run on the background lane; the scheduler singleton only publishes
(backup_tick). Every job opens its own connections — worker processes share
nothing with the API (same pattern as the reaper).
"""

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.config import settings as app_settings
from achilles.db.connections import DbConnections, close_connections, create_connections
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.lifecycle import db_beat, heartbeat_loop
from achilles.infra.redis import close_redis_pools, create_redis_pools
from achilles.infra.worker.base import Lane, publish
from achilles.knowledge_store.constants import (
    DESTRUCTIVE_WAIT_CAP_SECONDS,
    DESTRUCTIVE_WAIT_RETRY_SECONDS,
    BackupState,
    CurationState,
    CurationTrigger,
)
from achilles.knowledge_store.models import BackupSnapshot, CurationRun
from achilles.knowledge_store.services import (
    backup_schedule,
    backups,
    curation,
    curation_steps,
    maintenance,
    platform,
)
from achilles.knowledge_store.services.backup_storage import resolve_storage
from achilles.notifications.api import dispatch_from_worker

logger = logging.getLogger(__name__)


async def run_curation(ctx: Context, *, run_id: int) -> None:
    """Curation Pass chain: refs → merge (gated) → decay (lifecycle.html#curation-pass).

    A failed step doesn't stop the chain — the journal records per-step
    outcomes in `steps`; any step error makes the terminal state failed.
    """
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session, session.begin():
            started = await curation.mark_running(session, run_id)
        if not started:
            # The reaper freed the lock (or the run was cancelled) before we
            # got the slot — a new run may already be active, don't touch it.
            logger.warning("curation run %s is no longer queued — skipping", run_id)
            return
        await publish_board(redis.cache, Board.KNOWLEDGE)  # queued → running

        beat = db_beat(db.pg_session_factory, lambda s: curation.heartbeat(s, run_id))
        steps: dict[str, object] = {}
        errors: list[str] = []
        async with heartbeat_loop(beat):
            await _curation_step(
                db, steps, errors, "refs_materialized", curation_steps.materialize_refs
            )
            await _merge_step(db, run_id, steps, errors)
            await _curation_step(db, steps, errors, "entities_rescored", curation_steps.trust_decay)

        async with db.pg_session_factory() as session, session.begin():
            if errors:
                await curation.finish(
                    session,
                    run_id,
                    state=str(CurationState.FAILED),
                    steps=steps,
                    error=f"steps failed: {', '.join(errors)}",
                )
            else:
                await curation.finish(
                    session, run_id, state=str(CurationState.SUCCEEDED), steps=steps
                )
        await publish_board(redis.cache, Board.KNOWLEDGE)  # terminal state landed
        if errors:
            await dispatch_from_worker(
                db.pg_session_factory,
                event="system.curation_failed",
                source_ref=f"curation/{run_id}",
                dedup_key="curation_failed",
            )
    except Exception:
        logger.exception("curation run %s failed", run_id)
        async with db.pg_session_factory() as session, session.begin():
            await curation.finish(
                session, run_id, state=str(CurationState.FAILED), error="internal error"
            )
        await publish_board(redis.cache, Board.KNOWLEDGE)
        await dispatch_from_worker(
            db.pg_session_factory,
            event="system.curation_failed",
            source_ref=f"curation/{run_id}",
            dedup_key="curation_failed",
        )
    finally:
        await close_connections(db)
        await close_redis_pools(redis)


async def _curation_step(
    db: DbConnections,
    steps: dict[str, object],
    errors: list[str],
    name: str,
    step: Callable[[AsyncSession], Awaitable[int]],
) -> None:
    """Run one non-destructive step in its own transaction; record the outcome."""
    try:
        async with db.pg_session_factory() as session, session.begin():
            steps[name] = await step(session)
    except Exception:
        logger.exception("curation step %s failed", name)
        steps[name] = "error"
        errors.append(name)


async def _merge_step(
    db: DbConnections,
    run_id: int,
    steps: dict[str, object],
    errors: list[str],
    *,
    wait_retry: float = DESTRUCTIVE_WAIT_RETRY_SECONDS,
    wait_cap: float = DESTRUCTIVE_WAIT_CAP_SECONDS,
) -> None:
    """The destructive step: claim the window, merge, release — or skip.

    A running sync holds the gate; we retry up to the cap and then skip the
    step with a journal mark (the next scheduled run picks it up) —
    lifecycle.html#coordination.
    """
    deadline = asyncio.get_event_loop().time() + wait_cap
    acquired = False
    while True:
        async with db.pg_session_factory() as session, session.begin():
            acquired = await curation.open_destructive_window(session, run_id)
        if acquired or asyncio.get_event_loop().time() >= deadline:
            break
        await asyncio.sleep(wait_retry)
    if not acquired:
        steps["duplicates_merged"] = "skipped"  # syncs kept the lane busy the whole window
        return
    try:
        async with db.pg_session_factory() as session, session.begin():
            steps["duplicates_merged"] = await curation_steps.merge_duplicates(session)
    except Exception:
        logger.exception("curation step duplicates_merged failed")
        steps["duplicates_merged"] = "error"
        errors.append("duplicates_merged")
    finally:
        async with db.pg_session_factory() as session, session.begin():
            await curation.close_destructive_window(session, run_id)


async def run_reembed(ctx: Context, *, run_id: int) -> None:
    """Embedding refresh under a curation_runs journal (trigger=model_change)."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session, session.begin():
            started = await curation.mark_running(session, run_id)
        if not started:
            logger.warning("re-embed run %s is no longer queued — skipping", run_id)
            return
        await publish_board(redis.cache, Board.KNOWLEDGE)  # queued → running
        beat = db_beat(db.pg_session_factory, lambda s: curation.heartbeat(s, run_id))
        async with heartbeat_loop(beat):
            reembedded = await curation_steps.reembed_batches(
                db.pg_session_factory,
                notify=lambda: publish_board(redis.cache, Board.KNOWLEDGE),
            )
        async with db.pg_session_factory() as session, session.begin():
            await curation.finish(
                session,
                run_id,
                state=str(CurationState.SUCCEEDED),
                steps={"reembedded": reembedded},
            )
        await publish_board(redis.cache, Board.KNOWLEDGE)  # terminal state landed
    except curation_steps.EmbeddingRuntimeUnavailableError as exc:
        # Weights never became ready — fail honestly (chunks stay stale) rather
        # than a hollow success; a re-triggered model change resumes the tail.
        # The exception text carries the runtime's own diagnosis (load error,
        # loading past budget, silence) — that is what the Admin screen shows.
        logger.warning("re-embed run %s: %s", run_id, exc)
        async with db.pg_session_factory() as session, session.begin():
            await curation.finish(
                session,
                run_id,
                state=str(CurationState.FAILED),
                error=str(exc),
            )
        await publish_board(redis.cache, Board.KNOWLEDGE)
    except Exception:
        logger.exception("re-embed run %s failed", run_id)
        async with db.pg_session_factory() as session, session.begin():
            await curation.finish(
                session, run_id, state=str(CurationState.FAILED), error="internal error"
            )
        await publish_board(redis.cache, Board.KNOWLEDGE)
    finally:
        await close_connections(db)
        await close_redis_pools(redis)


async def run_backup(ctx: Context, *, snapshot_id: int) -> None:
    """pg_dump → storage → succeeded + retention rotation; any failure → failed + error."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        key = app_settings.derived_crypto_key()
        async with db.pg_session_factory() as session:
            settings_row = await backups.get_settings(session)
            storage = resolve_storage(
                settings_row.destination_url,
                creds_json=backups.decrypted_creds(settings_row, key=key),
            )
            retention = settings_row.retention_count

        started = datetime.now(UTC)
        beat = db_beat(db.pg_session_factory, lambda s: backups.heartbeat_snapshot(s, snapshot_id))

        with tempfile.TemporaryDirectory(prefix="achilles-backup-") as tmpdir:
            dump_path = Path(tmpdir) / "achilles.dump"
            async with heartbeat_loop(beat):
                await backups.dump_database(backups.libpq_dsn(app_settings.database_url), dump_path)
                size_bytes = dump_path.stat().st_size
                location = await storage.store(
                    dump_path, f"achilles-{started:%Y%m%dT%H%M%S}-{snapshot_id}.dump"
                )

        async with db.pg_session_factory() as session, session.begin():
            finished = await backups.finish_snapshot(
                session,
                snapshot_id,
                state=str(BackupState.SUCCEEDED),
                size_bytes=size_bytes,
                location=location,
            )
            if not finished:
                # Reaped mid-run: the lock is freed, the journal says failed —
                # don't resurrect the row; the stored dump stays unreferenced.
                logger.warning("backup snapshot %s was reaped mid-run: %s", snapshot_id, location)
            await backups.rotate_retention(session, storage, keep=retention)
        await publish_board(redis.cache, Board.KNOWLEDGE)  # terminal state landed
    except (ApiError, backups.BackupToolError, OSError) as exc:
        logger.exception("backup snapshot %s failed", snapshot_id)
        detail = getattr(exc, "detail", "") or str(exc)
        async with db.pg_session_factory() as session, session.begin():
            await backups.finish_snapshot(
                session,
                snapshot_id,
                state=str(BackupState.FAILED),
                error=detail[: backups.ERROR_TAIL],
            )
        await publish_board(redis.cache, Board.KNOWLEDGE)
        await dispatch_from_worker(
            db.pg_session_factory,
            event="system.backup_failed",
            source_ref=f"backup/{snapshot_id}",
            dedup_key="backup_failed",
        )
    finally:
        await close_connections(db)
        await close_redis_pools(redis)


async def run_restore(ctx: Context, *, snapshot_id: int) -> None:
    """Full-DB overwrite from a snapshot under maintenance mode (lifecycle.html#backup).

    Ingest and search pause behind the redis flag; other Postgres backends are
    terminated so pg_restore --clean can drop objects. The restore overwrites
    backup_snapshots itself — a known property of full-DB restore.
    """
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        # The route claimed the flag before publishing; from here the job owns
        # it — renewed with a short TTL from the heartbeat loop, cleared in the
        # finally. A hard worker death stops the renewals and the flag expires
        # on its own (RUN_ZOMBIE_AFTER) instead of gating the platform forever.
        try:
            async with db.pg_session_factory() as session:
                snapshot = await session.get(BackupSnapshot, snapshot_id)
                if snapshot is None or snapshot.location is None:
                    logger.error("restore: snapshot %s missing or has no location", snapshot_id)
                    return
                settings_row = await backups.get_settings(session)
                key = app_settings.derived_crypto_key()
                storage = resolve_storage(
                    settings_row.destination_url,
                    creds_json=backups.decrypted_creds(settings_row, key=key),
                )
                location = snapshot.location

            dump_path = await storage.fetch(location)

            async def beat() -> None:
                await maintenance.renew_maintenance(redis.durable)

            async with heartbeat_loop(beat):
                async with db.pg_engine.connect() as conn:
                    await conn.execute(
                        sa.text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                        )
                    )
                await db.pg_engine.dispose()  # pg_restore needs the field to itself
                await backups.restore_database(
                    backups.libpq_dsn(app_settings.database_url), dump_path
                )
            logger.info("restore from snapshot %s finished", snapshot_id)
        finally:
            await maintenance.exit_maintenance(redis.durable)
            # A restore rewrites the whole store — open boards refetch either way.
            await publish_board(redis.cache, Board.KNOWLEDGE)
    except ApiError, backups.BackupToolError, OSError:
        logger.exception("restore from snapshot %s failed", snapshot_id)
    finally:
        await close_connections(db)
        await close_redis_pools(redis)


async def curation_tick(ctx: Context) -> None:
    """Cron body on the scheduler singleton: due window → run row → publish.

    The cadence knobs are platform_settings.curation_frequency/weekday/time
    (the Admin "Knowledge Store" screen edits them) — the same org-local window
    mechanic as backups; the anchor is the last run's created_at regardless of
    outcome — a failing pass must not tighten the loop. MODEL_CHANGE rows are
    excluded: a re-embed run does no grooming, so it must not defer the pass.
    """
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            settings_row = await platform.get_platform_settings(session)
            cadence = backup_schedule.WindowCadence.for_curation(settings_row)
            last_created = await session.scalar(
                sa.select(sa.func.max(CurationRun.created_at)).where(
                    CurationRun.trigger != str(CurationTrigger.MODEL_CHANGE)
                )
            )
            fire = backup_schedule.is_due(
                cadence,
                last_started_at=last_created,
                timezone=settings_row.timezone,
                now=datetime.now(UTC),
            )
            if fire is None:
                return
            try:
                run_id = await curation.start_run(session, trigger=str(CurationTrigger.SCHEDULE))
            except ApiError:
                return  # an active run holds the platform lock — rejected, not queued
            await session.commit()
        await publish_board(redis.cache, Board.KNOWLEDGE)  # a queued row appeared
        await publish(
            app_settings.redis_durable_url,
            redis.durable,
            Lane.BACKGROUND,
            "run_curation",
            job_id=f"curation:{run_id}",
            run_id=run_id,
        )
    finally:
        await close_connections(db)
        await close_redis_pools(redis)


async def backup_tick(ctx: Context) -> None:
    """Cron body on the scheduler singleton: due window → snapshot row → publish.

    The correctness lock is the partial UNIQUE on backup_snapshots (a colliding
    start dies there); the redis dedup key only guards double publishes.
    """
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            settings_row = await backups.get_settings(session)
            if settings_row.destination_url is None:
                return
            last_started_at = await session.scalar(
                sa.select(sa.func.max(BackupSnapshot.started_at))
            )
            org_timezone = (await platform.get_platform_settings(session)).timezone
            fire = backup_schedule.is_due(
                backup_schedule.WindowCadence.for_backup(settings_row),
                last_started_at=last_started_at,
                timezone=org_timezone,
                now=datetime.now(UTC),
            )
            if fire is None:
                return
            try:
                snapshot_id = await backups.start_snapshot(session)
            except ApiError:
                return  # an active backup holds the lock — rejected, not queued
            await session.commit()
        await publish_board(redis.cache, Board.KNOWLEDGE)  # a queued row appeared

        # URL from the module settings alias (not the import-time global) —
        # same injection point as the connections above.
        await publish(
            app_settings.redis_durable_url,
            redis.durable,
            Lane.BACKGROUND,
            "run_backup",
            job_id=f"backup:{fire:%Y%m%dT%H%M}",
            snapshot_id=snapshot_id,
        )
    finally:
        await close_connections(db)
        await close_redis_pools(redis)
