"""Per-identity API rate limit: token bucket, tiered by role, 60 rpm per api-key.

API throttling is fail-open by design (rate-limit.html#consumers) — a Redis
outage must not take the API down; the brute-force barrier stays fail-closed.
"""

import logging

from fastapi import Response
from redis.asyncio import Redis
from redis.exceptions import RedisError

from achilles.api.problems import rate_limited
from achilles.infra.rate_limit import hit_token_bucket

logger = logging.getLogger(__name__)

RATE_LIMIT_REMAINING_HEADER = "X-RateLimit-Remaining"


async def enforce_identity_rate_limit(
    redis: Redis,
    *,
    bucket_key: str,
    rpm: int,
    now: float,
    response: Response,
) -> None:
    try:
        decision = await hit_token_bucket(
            redis, bucket_key, capacity=rpm, refill_per_minute=rpm, now=now
        )
    except RedisError:
        logger.warning("Rate-limit store unavailable — API throttling fails open")
        return
    response.headers[RATE_LIMIT_REMAINING_HEADER] = str(max(decision.remaining, 0))
    if not decision.allowed:
        raise rate_limited(decision.retry_after, "API rate limit exceeded")
