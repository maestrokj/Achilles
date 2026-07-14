"""Cron singleton: SAQ's built-in CronJob on a dedicated 1-replica service.

Run: `saq achilles.infra.scheduler.settings.settings` — exactly one replica
(scheduling.html#cron: were cron on every worker, N replicas would fire N times).
The scheduler only publishes to lanes; workers drain. The stale-heartbeat
reaper also lives here — one sweeper, no cleanup races (lifecycle.html).
The 1-replica guarantee is also why the Mattermost WebSocket listener boards
this process (mattermost/index.html): exactly one connection, no coordination.

A double tick is killed by the Postgres uniqueness lock on the run table;
a missed tick is not backfilled — the next window simply comes.
"""

from typing import Any

from saq import CronJob, Queue
from saq.types import Context

from achilles.agent_engine.scheduler.tick import agents_tick
from achilles.config import settings as app_settings
from achilles.db.connections import close_connections, create_connections
from achilles.harvester.jobs import health_tick, reconcile_tick, sync_tick
from achilles.infra import lifecycle
from achilles.knowledge_store.jobs import backup_tick, curation_tick
from achilles.mattermost.listener import listener_shutdown, listener_startup
from achilles.notifications.jobs import notifications_tick

REAPER_CRON = "*/1 * * * *"  # sweep every minute; per-table stale thresholds in RUN_TABLES
BACKUP_TICK_CRON = "*/1 * * * *"  # due-ness check is cheap; the window math lives in KS
SYNC_TICK_CRON = "*/1 * * * *"  # per-source cadence math lives in harvester/services/schedule
RECONCILE_TICK_CRON = "*/1 * * * *"  # the weekly window is one minute wide — check every one
HEALTH_TICK_CRON = "7 * * * *"  # hourly fan-out; the probe itself is daily per source
CURATION_TICK_CRON = "*/1 * * * *"  # cheap due-ness check (2 SELECTs, no-op off-window); minute cadence keeps Admin responsive after a window edit  # noqa: E501
AGENTS_TICK_CRON = "*/1 * * * *"  # next_run_at is minute-grained (HH:MM slots)
NOTIFICATIONS_TICK_CRON = "*/5 * * * *"  # thresholds are day-grained; the sweep tolerates 5 min
SCHEDULER_QUEUE = "scheduler"  # own queue: the singleton never drains lane work


async def reap_stale_runs(ctx: Context) -> int:
    del ctx
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session, session.begin():
            return await lifecycle.reap_stale_runs(session)
    finally:
        await close_connections(db)


# Each cron only PUBLISHES to a lane.
settings: dict[str, Any] = {
    "queue": Queue.from_url(app_settings.redis_durable_url, name=SCHEDULER_QUEUE),
    "functions": [
        reap_stale_runs,
        backup_tick,
        curation_tick,
        sync_tick,
        reconcile_tick,
        health_tick,
        agents_tick,
        notifications_tick,
    ],
    "cron_jobs": [
        CronJob(reap_stale_runs, cron=REAPER_CRON),
        CronJob(backup_tick, cron=BACKUP_TICK_CRON),
        CronJob(curation_tick, cron=CURATION_TICK_CRON),
        CronJob(sync_tick, cron=SYNC_TICK_CRON),
        CronJob(reconcile_tick, cron=RECONCILE_TICK_CRON),
        CronJob(health_tick, cron=HEALTH_TICK_CRON),
        CronJob(agents_tick, cron=AGENTS_TICK_CRON),
        CronJob(notifications_tick, cron=NOTIFICATIONS_TICK_CRON),
    ],
    "concurrency": 1,
    # The Mattermost listener rides the singleton's lifecycle, not its queue.
    "startup": [listener_startup],
    "shutdown": [listener_shutdown],
}
