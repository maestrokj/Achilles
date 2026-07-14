"""SAQ job: run_sync — the background-lane body of every sync mode.

Same shape as knowledge_store jobs: the job opens its own connections from
module-level settings (workers share nothing with the API), journals through
harvester.services.sync_runs and beats under heartbeat_loop. Scheduler tick
bodies (sync/reconcile/health) live here too from stage-5 slice 9.
"""

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from redis.asyncio import Redis
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.api.problems import ApiError
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt
from achilles.config import settings as app_settings
from achilles.db.connections import close_connections, create_connections
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.harvester.connectors.base import BaseConnector
from achilles.harvester.connectors.registry import get_connector_type
from achilles.harvester.constants import (
    CHECKPOINT_FRESHNESS,
    SYNC_FAILURE_SERIES,
    SyncMode,
    SyncState,
    SyncTrigger,
)
from achilles.harvester.models import SyncRun
from achilles.harvester.pipeline.runner import SyncOutcome, execute_run
from achilles.harvester.pipeline.throttle import SourceThrottle
from achilles.harvester.services import schedule, sync_runs
from achilles.harvester.services.sources import (
    create_connector,
    decrypt_credential,
    probe_status_from,
    stamp_probe,
)
from achilles.infra.lifecycle import db_beat, heartbeat_loop, wait_for_gate
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.infra.worker.base import Lane, publish
from achilles.knowledge_store.constants import ProbeStatus
from achilles.knowledge_store.models import Source
from achilles.knowledge_store.services import platform
from achilles.notifications.api import dispatch_from_worker

logger = logging.getLogger(__name__)


async def run_sync(ctx: Context, *, run_id: int) -> None:
    """Wait out the lane gate, execute the pipeline, close the journal."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        prep = await _prepare(db.pg_session_factory, redis.durable, run_id)
        if prep is None:
            return

        source_id, mode, scope, since, initial_done, connector = prep
        try:  # from here on the connector (open httpx client) must not leak
            # A blocked sync waits in queued out a curation destructive window
            # (lifecycle.html#coordination).
            gate = await wait_for_gate(
                db.pg_session_factory,
                try_start=lambda s: sync_runs.mark_running(s, run_id),
                get_state=lambda s: sync_runs.get_state(s, run_id),
                heartbeat=lambda s: sync_runs.heartbeat(s, run_id),
                queued_state=str(SyncState.QUEUED),
            )
            if gate is False:
                logger.warning("sync run %s is no longer queued — skipping", run_id)
                return
            if gate is None:
                async with db.pg_session_factory() as session, session.begin():
                    await sync_runs.finish(
                        session,
                        run_id,
                        state=str(SyncState.FAILED),
                        error_detail="lane gate wait timed out",
                    )
                await publish_board(redis.cache, Board.HARVESTER)
                return
            await publish_board(redis.cache, Board.HARVESTER)  # queued → running

            beat = db_beat(db.pg_session_factory, lambda s: sync_runs.heartbeat(s, run_id))
            async with heartbeat_loop(beat):
                outcome = await execute_run(
                    db.pg_session_factory,
                    connector,
                    run_id=run_id,
                    source_id=source_id,
                    mode=mode,
                    scope=scope,
                    since=since,
                    initial_done=initial_done,
                    notify=lambda: publish_board(redis.cache, Board.HARVESTER),
                )

            async with db.pg_session_factory() as session, session.begin():
                await _close_journal(session, run_id, source_id, mode, scope, outcome)
            await publish_board(redis.cache, Board.HARVESTER)  # terminal state landed
            await _notify_sync_outcome(
                db.pg_session_factory,
                redis,
                source_id=source_id,
                state=outcome.state,
                errors=outcome.errors,
            )
        finally:
            await connector.aclose()
    except Exception:
        logger.exception("sync run %s failed", run_id)
        async with db.pg_session_factory() as session, session.begin():
            await sync_runs.finish(
                session, run_id, state=str(SyncState.FAILED), error_detail="internal error"
            )
        await publish_board(redis.cache, Board.HARVESTER)
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def _prepare(
    session_factory: async_sessionmaker[AsyncSession], redis: Redis, run_id: int
) -> tuple[int, str, dict[str, object] | None, datetime | None, int, BaseConnector] | None:
    """Load run + source, build the connector, derive `since` and resume state."""
    async with session_factory() as session:
        run = await session.get(SyncRun, run_id)
        if run is None:
            logger.error("sync run %s not found", run_id)
            return None
        source = await session.get(Source, run.source_id)
        if source is None:
            logger.error("sync run %s: source %s is gone", run_id, run.source_id)
            return None

        connector_cls = get_connector_type(source.connector_type)
        if connector_cls is None:
            await sync_runs.finish(
                session,
                run_id,
                state=str(SyncState.FAILED),
                error_detail=f"unknown connector type {source.connector_type!r}",
            )
            await session.commit()
            return None

        credential = ""
        if source.credential_enc:
            key = app_settings.derived_crypto_key()
            try:
                credential = decrypt(source.credential_enc, key=key)
            except CiphertextInvalidError:
                await sync_runs.finish(
                    session,
                    run_id,
                    state=str(SyncState.FAILED),
                    error_detail="stored credential cannot be decrypted",
                )
                await session.commit()
                return None

        since, initial_done = await _resume_point(
            session, run, source, ordered_stream=connector_cls.manifest.ordered_stream
        )

        throttle = SourceThrottle(
            redis,
            scope_key=f"{connector_cls.manifest.rate_limit_scope}:{source.id}",
            base_rate_per_second=connector_cls.manifest.rate_limit_per_second,
        )
        connector = create_connector(
            connector_cls, source, credential=credential, throttle=throttle
        )
        scope = dict(run.scope) if run.scope else None
        return source.id, run.mode, scope, since, initial_done, connector


async def _resume_point(
    session: AsyncSession, run: SyncRun, source: Source, *, ordered_stream: bool
) -> tuple[datetime | None, int]:
    """Fresh checkpoint of the last failed whole-source incremental run → resume.

    Only a whole-source incremental run may resume, and only on a globally
    ordered stream: a resumed reconciliation would mass soft-delete everything
    it skipped, a targeted run's watermark covers only its items, and an
    unordered stream would skip older items in later containers. A checkpoint
    older than the stored cursor lost to a newer succeeded run — ignore it.
    """
    cursor_since = (
        _parse_iso(str((source.incremental_cursor or {}).get("since", "")))
        if run.mode == str(SyncMode.INCREMENTAL)
        else None
    )
    resumable = run.mode == str(SyncMode.INCREMENTAL) and run.scope is None and ordered_stream
    if resumable:
        last = await session.scalar(
            sa.select(SyncRun)
            .where(
                SyncRun.source_id == source.id,
                SyncRun.mode == run.mode,
                SyncRun.state == str(SyncState.FAILED),
                SyncRun.scope.is_(None),
                SyncRun.id != run.id,
            )
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        checkpoint = last.checkpoint if last is not None else None
        if checkpoint:
            saved_at = _parse_iso(str(checkpoint.get("saved_at", "")))
            watermark = _parse_iso(str(checkpoint.get("watermark", "")))
            fresh = saved_at and datetime.now(UTC) - saved_at < CHECKPOINT_FRESHNESS
            if fresh and watermark and (cursor_since is None or watermark > cursor_since):
                return watermark, int(checkpoint.get("done", 0) or 0)

    if run.mode == str(SyncMode.INCREMENTAL):
        return cursor_since, 0
    return None, 0  # full / reconciliation: the whole source


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _notify_sync_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    redis: RedisPools,
    *,
    source_id: int,
    state: str,
    errors: int,
) -> None:
    """Raise the catalog events a finished run may warrant (dispatcher.html#catalog).

    A notification must never fail the run itself — the journal is already closed.
    """
    try:
        async with session_factory() as session:
            source = await session.get(Source, source_id)
            if source is None:
                return
            if state == str(SyncState.FAILED):
                last_states = (
                    await session.scalars(
                        sa.select(SyncRun.state)
                        .where(SyncRun.source_id == source_id, SyncRun.finished_at.is_not(None))
                        .order_by(SyncRun.id.desc())
                        .limit(SYNC_FAILURE_SERIES)
                    )
                ).all()
                is_series = len(last_states) >= SYNC_FAILURE_SERIES and all(
                    run_state == str(SyncState.FAILED) for run_state in last_states
                )
                if is_series:
                    await dispatch_from_worker(
                        session_factory,
                        redis=redis,
                        event="sync.run_failure_series",
                        source_ref=f"source/{source_id}",
                        params={"source_name": source.name, "count": str(SYNC_FAILURE_SERIES)},
                        dedup_key=f"syncfail:{source_id}",
                    )
            elif state == str(SyncState.SUCCEEDED) and errors > 0:
                await dispatch_from_worker(
                    session_factory,
                    redis=redis,
                    event="sync.run_with_losses",
                    source_ref=f"source/{source_id}",
                    params={"source_name": source.name, "errors": str(errors)},
                    dedup_key=f"synclosses:{source_id}",
                )
    except Exception:
        logger.warning("sync outcome notification for source %s failed", source_id, exc_info=True)


async def _close_journal(
    session: AsyncSession,
    run_id: int,
    source_id: int,
    mode: str,
    scope: dict[str, object] | None,
    outcome: SyncOutcome,
) -> None:
    """Terminal write + cursor advance (only on success — sync-modes.html#incremental)."""
    await sync_runs.update_progress(
        session,
        run_id,
        entities_done=outcome.done,
        # A succeeded/cancelled run needs no resume point; a failed one keeps
        # its stored checkpoint so the next run can pick up from it.
        checkpoint=sync_runs.UNSET if outcome.state == str(SyncState.FAILED) else None,
        error_count=outcome.errors,
    )
    if outcome.state == str(SyncState.CANCELLED):
        return  # the API already terminalized the row; don't falsify it
    await sync_runs.finish(session, run_id, state=outcome.state, error_detail=outcome.error_detail)
    advance = (
        outcome.state == str(SyncState.SUCCEEDED)
        and outcome.watermark is not None
        and scope is None  # a targeted run's watermark covers only its items
        and mode != str(SyncMode.RECONCILIATION)  # reconciliation doesn't move the cursor
    )
    if advance:
        # Monotonic advance only: an overlap-window re-fetch (timezone skew)
        # must never pull the cursor backwards.
        current = await session.scalar(
            sa.select(Source.incremental_cursor).where(Source.id == source_id)
        )
        existing = _parse_iso(str((current or {}).get("since", "")))
        new_since = _parse_iso(outcome.watermark or "")
        if new_since is not None and (existing is None or new_since > existing):
            await session.execute(
                sa.update(Source)
                .where(Source.id == source_id)
                .values(incremental_cursor={"since": outcome.watermark})
            )


# --- Scheduler tick bodies (publish-only, run on the cron singleton) ---


async def sync_tick(ctx: Context) -> None:
    """Per-source incremental cadence + the watchdog escalation (sync-modes.html)."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            platform_row = await platform.get_platform_settings(session)
            if platform_row.maintenance_mode:
                return  # org maintenance pauses scheduled launches, not running work
            rows = (await session.scalars(sa.select(Source).order_by(Source.id))).all()
            for source in rows:
                active = await sync_runs.active_run(session, source.id)
                last = await sync_runs.last_finished(session, source.id)
                last_success = await session.scalar(
                    sa.select(sa.func.max(SyncRun.finished_at)).where(
                        SyncRun.source_id == source.id,
                        SyncRun.state == str(SyncState.SUCCEEDED),
                    )
                )
                plan = schedule.sync_due(
                    source,
                    platform_row,
                    # created_at fallback: a reaped run predating the reaper's
                    # finished_at stamp must not read as "never synced".
                    last_run_at=(last.finished_at or last.created_at) if last else None,
                    last_success_at=last_success,
                    has_active_run=active is not None,
                )
                if plan is None:
                    continue
                await _tick_start(session, redis, source.id, plan)
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def reconcile_tick(ctx: Context) -> None:
    """Weekly full sweep in the org-time minute-of-week window."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            platform_row = await platform.get_platform_settings(session)
            if platform_row.maintenance_mode:
                return  # org maintenance pauses scheduled launches, not running work
            rows = (await session.scalars(sa.select(Source).order_by(Source.id))).all()
            for source in rows:
                active = await sync_runs.active_run(session, source.id)
                last_reconcile = await session.scalar(
                    sa.select(sa.func.max(SyncRun.created_at)).where(
                        SyncRun.source_id == source.id,
                        SyncRun.mode == str(SyncMode.RECONCILIATION),
                    )
                )
                due = schedule.reconcile_due(
                    source,
                    platform_row,
                    last_reconcile_at=last_reconcile,
                    has_active_run=active is not None,
                )
                if not due:
                    continue
                plan = schedule.DuePlan(
                    mode=str(SyncMode.RECONCILIATION), trigger=str(SyncTrigger.SCHEDULE)
                )
                await _tick_start(session, redis, source.id, plan)
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def _tick_start(
    session: AsyncSession, redis: RedisPools, source_id: int, plan: schedule.DuePlan
) -> None:
    """Journal row (guarded by the per-source lock) → publish; a loser tick is silent."""
    try:
        run_id = await sync_runs.start_run(
            session, source_id=source_id, mode=plan.mode, trigger=plan.trigger
        )
    except ApiError:
        await session.rollback()
        return  # a concurrent start holds the lock — rejected, not queued
    await session.commit()
    await publish_board(redis.cache, Board.HARVESTER)  # a queued row appeared
    await publish(
        app_settings.redis_durable_url,
        redis.durable,
        Lane.BACKGROUND,
        "run_sync",
        job_id=f"sync:{run_id}",
        run_id=run_id,
    )


async def health_tick(ctx: Context) -> None:
    """Daily light probe fan-out; the probe itself runs on the background lane."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            rows = (await session.scalars(sa.select(Source).order_by(Source.id))).all()
            due_ids = [source.id for source in rows if schedule.probe_due(source)]
        for source_id in due_ids:
            await publish(
                app_settings.redis_durable_url,
                redis.durable,
                Lane.BACKGROUND,
                "run_probe",
                job_id=f"probe:{source_id}:{datetime.now(UTC):%Y%m%d}",
                source_id=source_id,
            )
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def run_probe(ctx: Context, *, source_id: int) -> None:
    """The scheduled light probe body: check_connection → last_probe_* stamp."""
    del ctx
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            source = await session.get(Source, source_id)
            if source is None:
                return
            connector_cls = get_connector_type(source.connector_type)
            if connector_cls is None:
                return
            key = app_settings.derived_crypto_key()
            connector = create_connector(
                connector_cls, source, credential=decrypt_credential(source, key=key)
            )
            try:
                diagnosis = await connector.check_connection()
            finally:
                await connector.aclose()
            was_ok = source.last_probe_status in (None, str(ProbeStatus.OK))
            status = probe_status_from(diagnosis)
            stamp_probe(source, status)
            await session.commit()
            if was_ok and status != str(ProbeStatus.OK):
                # The transition (not the steady red) raises the alarm; the
                # facade opens Redis pools only on this rare path.
                await dispatch_from_worker(
                    db.pg_session_factory,
                    event="sync.source_unreachable",
                    source_ref=f"source/{source_id}",
                    params={"source_name": source.name},
                    dedup_key=f"probe:{source_id}",
                )
    except Exception:
        logger.exception("probe of source %s failed", source_id)
    finally:
        await close_connections(db)
