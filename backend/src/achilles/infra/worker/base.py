"""SAQ plumbing shared by the three lanes.

Design: cache-workers/_workzone/queues.html. Lanes are isolation classes by
workload character, not priorities; queue payload lives on redis-durable
(SAQ builds its own raw-bytes connection from the URL — never reuse the app's
decode_responses client).
"""

from enum import StrEnum
from typing import Any

from redis.asyncio import Redis
from saq import Queue

from achilles.config import settings
from achilles.infra.redis import PREFIX_DEDUP


class Lane(StrEnum):
    INTERACTIVE = "interactive"  # fast · low latency (email tx, messenger inbound)
    BACKGROUND = "background"  # heavy & bulky (sync, embedding, curation, backup)
    AGENTS = "agents"  # own LLM-call ceiling, separate from live chat


# Slot counts live in Settings (WORKER_CONCURRENCY_* env) so a deployment
# tunes them to its hardware without a code change; the rationale for the
# defaults sits next to them in config.py.
LANE_CONCURRENCY: dict[Lane, int] = {
    Lane.INTERACTIVE: settings.worker_concurrency_interactive,
    Lane.BACKGROUND: settings.worker_concurrency_background,
    Lane.AGENTS: settings.worker_concurrency_agents,
}

DEDUP_SUCCESS_TTL_SECONDS = 24 * 60 * 60  # a completed job_id stays claimed for a day

# SAQ's per-job default timeout is 10 seconds — its sweeper would abort every
# long-running job (sync, curation, an agent's LLM loop). Staleness is owned by
# the domain-level heartbeat reaper (infra/lifecycle.py), so SAQ's is disabled.
JOB_TIMEOUT_DISABLED = 0


def make_queue(lane: Lane) -> Queue:
    return Queue.from_url(settings.redis_durable_url, name=lane.value)


def lane_settings(lane: Lane, functions: list[Any], **extra: object) -> dict[str, Any]:
    """The dict the `saq` CLI consumes: same image, different command per lane."""
    return {
        "queue": make_queue(lane),
        "functions": functions,
        "concurrency": LANE_CONCURRENCY[lane],
        **extra,
    }


async def enqueue_idempotent(
    queue: Queue,
    redis: Redis,
    function_name: str,
    *,
    job_id: str,
    **kwargs: object,
) -> bool:
    """Enqueue once per job_id.

    A retry (webhook redelivery, double cron tick) must not double the work —
    queues.html#registry.
    """
    claimed = await redis.set(
        f"{PREFIX_DEDUP}job:{job_id}",
        "1",
        nx=True,
        ex=DEDUP_SUCCESS_TTL_SECONDS,
    )
    if not claimed:
        return False
    await queue.enqueue(function_name, key=job_id, timeout=JOB_TIMEOUT_DISABLED, **kwargs)
    return True


async def release_claim(redis: Redis, *, job_id: str) -> None:
    """Drop an enqueue claim so the next publish of this job_id goes through.

    For sweeps re-publishing lost work: the claim outlives a crash between
    SET and enqueue (and a job that failed for good), while SAQ's own
    incomplete-set keeps deduping a job that is merely slow.
    """
    await redis.delete(f"{PREFIX_DEDUP}job:{job_id}")


async def publish(
    queue_url: str,
    redis: Redis,
    lane: Lane,
    function_name: str,
    *,
    job_id: str,
    **kwargs: object,
) -> bool:
    """One-shot idempotent publish: connect → enqueue once per job_id → disconnect.

    Publishers (routes, cron ticks) hold no long-lived queue; the URL comes from
    their own injection point (app state / module settings), never the import-time
    global.
    """
    queue = Queue.from_url(queue_url, name=lane.value)
    try:
        return await enqueue_idempotent(queue, redis, function_name, job_id=job_id, **kwargs)
    finally:
        await queue.disconnect()
