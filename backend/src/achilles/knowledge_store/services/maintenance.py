"""Maintenance mode: restore overwrites the whole DB, so ingest + search pause.

The flag lives in redis-durable (a platform gate is a lock, not cache); retrieval
routes take `ensure_not_maintenance` and answer 503 while a restore is running.

The flag doubles as the restore single-flight lock: the route claims it
atomically (SET NX) before publishing, so two concurrent restore requests
cannot both reach pg_restore. Every write carries a TTL — the running job
renews it from its heartbeat loop, so a hard worker death (SIGKILL mid
pg_restore) self-heals instead of leaving the platform in 503 forever.
"""

from datetime import timedelta

from fastapi import Request
from redis.asyncio import Redis

from achilles.api.problems import ApiError
from achilles.infra.lifecycle import RUN_ZOMBIE_AFTER
from achilles.infra.redis import PREFIX_LOCK
from achilles.knowledge_store.constants import CODE_MAINTENANCE

MAINTENANCE_KEY = f"{PREFIX_LOCK}maintenance"
# The claim must survive the queue-pickup gap (route → worker); once the job
# runs, its heartbeat loop re-asserts the flag with the short zombie TTL.
MAINTENANCE_CLAIM_TTL = timedelta(minutes=5)


async def enter_maintenance(redis: Redis, *, ttl: timedelta = MAINTENANCE_CLAIM_TTL) -> bool:
    """Atomically claim the flag; False → a restore already holds it."""
    return bool(await redis.set(MAINTENANCE_KEY, "1", nx=True, ex=int(ttl.total_seconds())))


async def renew_maintenance(redis: Redis, *, ttl: timedelta = RUN_ZOMBIE_AFTER) -> None:
    """(Re)assert the flag — the running restore owns it regardless of who claimed."""
    await redis.set(MAINTENANCE_KEY, "1", ex=int(ttl.total_seconds()))


async def exit_maintenance(redis: Redis) -> None:
    await redis.delete(MAINTENANCE_KEY)


async def is_maintenance(redis: Redis) -> bool:
    return bool(await redis.exists(MAINTENANCE_KEY))


async def ensure_not_maintenance(request: Request) -> None:
    if await is_maintenance(request.state.redis.durable):
        raise ApiError(
            503,
            CODE_MAINTENANCE,
            "Maintenance in progress",
            "The knowledge store is being restored from a snapshot; retry shortly.",
        )
