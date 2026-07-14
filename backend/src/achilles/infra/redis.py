"""Two Redis instances: durable (AOF, noeviction) and cache (LRU, no persistence).

Eviction-by-memory is incompatible with durable data (queues, counters, locks),
so the roles are split physically into two processes — see
docs/architecture/modules/cache-workers/_workzone/redis-keys.html#instances.
"""

import logging
from dataclasses import dataclass

from redis.asyncio import Redis

from achilles.config import Settings

logger = logging.getLogger(__name__)

# Key-prefix registry — mirrors redis-keys.html#registry.
# A new key means a new constant here, not a free-form name at the call site.
PREFIX_QUEUE = "q:"  # durable · SAQ queue payload
PREFIX_RATE_LIMIT = "rl:"  # durable · rate-limit counters
PREFIX_BRUTE = "brute:"  # durable · brute-force barrier state
PREFIX_GRACE = "grace:"  # durable · refresh-rotation grace pairs
PREFIX_BLACKLIST = "bl:"  # durable · jti blacklist (v2)
PREFIX_LOCK = "lock:"  # durable · single-flight locks
PREFIX_DEDUP = "dedup:"  # durable · dedup windows
PREFIX_CACHE = "cache:"  # cache · evictable derived data
PREFIX_PUSH = "push:"  # cache · pub/sub channels


@dataclass(frozen=True, slots=True)
class RedisPools:
    """App-side clients; SAQ builds its own raw-bytes connection from the URL."""

    durable: Redis
    cache: Redis


def create_redis_pools(settings: Settings) -> RedisPools:
    def client(url: str) -> Redis:
        return Redis.from_url(
            url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
        )

    return RedisPools(
        durable=client(settings.redis_durable_url),
        cache=client(settings.redis_cache_url),
    )


async def close_redis_pools(pools: RedisPools) -> None:
    for name, client in (("redis-durable", pools.durable), ("redis-cache", pools.cache)):
        try:
            await client.aclose()
        except Exception:
            logger.warning("Failed to close %s cleanly", name, exc_info=True)
