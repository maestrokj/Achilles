"""Atomic rate-limit primitives on redis-durable: sliding window + token bucket.

Lua counts and debits in one round-trip — no races between workers
(cache-workers/_workzone/rate-limit.html#primitive). ``now`` comes from the
caller so tests can time-travel; fail-mode on a Redis outage is the caller's
choice (safe default is closed) — rate-limit.html#consumers.
"""

import math
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

_MS_PER_SECOND = 1000

_WINDOW_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if oldest[2] then retry_ms = tonumber(oldest[2]) + window_ms - now_ms end
  return {0, 0, retry_ms}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, limit - count - 1, 0}
"""

_BUCKET_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_per_ms = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now_ms
end
tokens = math.min(capacity, tokens + math.max(0, now_ms - ts) * refill_per_ms)
local allowed = 0
local retry_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_ms = math.ceil((cost - tokens) / refill_per_ms)
end
redis.call('HSET', key, 'tokens', tostring(tokens), 'ts', tostring(now_ms))
redis.call('PEXPIRE', key, math.ceil(capacity / refill_per_ms))
return {allowed, math.floor(tokens), retry_ms}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: int
    """Whole seconds (rounded up) — feeds the Retry-After header and body field."""


def _decision(raw: object) -> RateLimitDecision:
    allowed, remaining, retry_ms = (int(v) for v in raw)  # type: ignore[union-attr, call-overload]
    return RateLimitDecision(
        allowed=bool(allowed),
        remaining=remaining,
        retry_after=math.ceil(retry_ms / _MS_PER_SECOND),
    )


async def hit_sliding_window(
    redis: Redis,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    now: float,
    member: str | None = None,
) -> RateLimitDecision:
    """Count an event in a sliding window; refuse once `limit` events fit the window."""
    now_ms = int(now * _MS_PER_SECOND)
    member = member or f"{now_ms}-{secrets.token_hex(4)}"
    raw = await redis.eval(  # type: ignore[misc]
        _WINDOW_LUA,
        1,
        key,
        str(now_ms),
        str(window_seconds * _MS_PER_SECOND),
        str(limit),
        member,
    )
    return _decision(raw)


async def hit_token_bucket(
    redis: Redis,
    key: str,
    *,
    capacity: int,
    refill_per_minute: float,
    now: float,
    cost: int = 1,
) -> RateLimitDecision:
    """Debit a token bucket: bursts up to `capacity`, sustained rate `refill_per_minute`."""
    now_ms = int(now * _MS_PER_SECOND)
    refill_per_ms = refill_per_minute / (60 * _MS_PER_SECOND)
    raw = await redis.eval(  # type: ignore[misc]
        _BUCKET_LUA,
        1,
        key,
        str(now_ms),
        str(capacity),
        repr(refill_per_ms),
        str(cost),
    )
    return _decision(raw)
