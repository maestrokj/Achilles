"""Three-layer brute-force barrier — protection.html#brute-force.

Layer 1: sliding IP window (20 / 15 min). Layer 2: per-account exponential
delay from the 3rd failure (1s → cap 30s), enforced as a stored not-before —
429 + Retry-After, no held connections. Layer 3: alert hook at 10 failures.
State lives on redis-durable; the barrier is fail-closed by design (a Redis
outage blocks login attempts rather than waving them through).
"""

import logging
import math
from datetime import datetime

from redis.asyncio import Redis

from achilles.api.problems import ApiError
from achilles.api.problems import rate_limited as _rate_limited
from achilles.auth.constants import (
    BRUTE_ACCOUNT_BASE_DELAY,
    BRUTE_ACCOUNT_FREE_ATTEMPTS,
    BRUTE_ACCOUNT_MAX_DELAY,
    BRUTE_ALERT_THRESHOLD,
    BRUTE_IP_LIMIT,
    BRUTE_IP_WINDOW,
)
from achilles.auth.security.tokens import hash_token
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import PREFIX_BRUTE

logger = logging.getLogger(__name__)

_IP_KEY = PREFIX_BRUTE + "ip:{ip}"
_ACCOUNT_KEY = PREFIX_BRUTE + "account:{email_hash}"

_COUNT_FIELD = "count"
_NOT_BEFORE_FIELD = "not_before"


def hash_email(email: str) -> str:
    """lower(email) → the same SHA-256-at-rest digest as tokens; a Redis dump leaks no addresses."""
    return hash_token(email.lower())


def _account_key(email: str) -> str:
    return _ACCOUNT_KEY.format(email_hash=hash_email(email))


def rate_limited(retry_after: int) -> ApiError:
    return _rate_limited(retry_after, "Too many attempts. Try again later.")


async def check_ip(redis: Redis, ip: str, *, now: datetime) -> None:
    decision = await hit_sliding_window(
        redis,
        _IP_KEY.format(ip=ip),
        limit=BRUTE_IP_LIMIT,
        window_seconds=int(BRUTE_IP_WINDOW.total_seconds()),
        now=now.timestamp(),
    )
    if not decision.allowed:
        raise rate_limited(decision.retry_after)


async def check_account_delay(redis: Redis, email: str, *, now: datetime) -> None:
    raw = await redis.hget(_account_key(email), _NOT_BEFORE_FIELD)  # type: ignore[misc]
    if raw is None:
        return
    not_before = float(raw)
    if now.timestamp() < not_before:
        raise rate_limited(math.ceil(not_before - now.timestamp()))


async def record_failure(redis: Redis, email: str, *, now: datetime) -> int:
    """Count a failure; from the 3rd one arm the exponential delay. Returns the count."""
    key = _account_key(email)
    count = int(await redis.hincrby(key, _COUNT_FIELD, 1))  # type: ignore[misc]
    pipe = redis.pipeline()
    if count > BRUTE_ACCOUNT_FREE_ATTEMPTS:
        exponent = count - BRUTE_ACCOUNT_FREE_ATTEMPTS - 1
        delay = min(
            BRUTE_ACCOUNT_BASE_DELAY.total_seconds() * 2**exponent,
            BRUTE_ACCOUNT_MAX_DELAY.total_seconds(),
        )
        pipe.hset(key, _NOT_BEFORE_FIELD, str(now.timestamp() + delay))
    pipe.expire(key, BRUTE_IP_WINDOW)
    await pipe.execute()
    if alert_due(count):
        # The Notifications alert is raised by the route (alert_brute_force):
        # this Redis-only context has no queue plumbing of its own.
        logger.warning("Brute-force alert: %s login failures for one account", count)
    return count


def alert_due(count: int) -> bool:
    """True exactly once per streak — at the alert threshold."""
    return count == BRUTE_ALERT_THRESHOLD


async def reset(redis: Redis, email: str) -> None:
    await redis.delete(_account_key(email))
