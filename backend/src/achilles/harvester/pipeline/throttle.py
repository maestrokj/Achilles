"""Adaptive source pacing: AIMD-lite + Retry-After + header budget (reliability.html#rate).

State lives in redis-durable per manifest scope, so the learned capacity
survives worker restarts:

- ``throttle:rate:{scope}``   — current pace (req/s), the AIMD-learned value
- ``throttle:bucket:{scope}`` — token bucket debited by acquire (infra Lua)
- ``throttle:pause:{scope}``  — Retry-After park: all requests on the scope wait

AIMD-lite: every AIMD_WINDOW successes → x1.1 (up to x4 of the manifest pace);
a 429 → x0.5 (down to x0.1 of it). (X-)RateLimit-Remaining/Reset clamp the pace
to ``safe_rps = remaining / (reset - now)`` when the source volunteers them.
Rate updates are read-modify-write, not atomic — an accepted simplification
for the single background worker; move to Lua if workers scale out.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis

from achilles.harvester.connectors.http import retry_after_seconds
from achilles.infra.rate_limit import hit_token_bucket

logger = logging.getLogger(__name__)

AIMD_WINDOW = 20  # successes between additive increases
AIMD_INCREASE = 1.1
AIMD_DECREASE = 0.5
RATE_FLOOR_FACTOR = 0.1  # never learn below x0.1 of the manifest pace
RATE_CEILING_FACTOR = 4.0  # never probe above x4 of it
RATE_TTL_SECONDS = 6 * 60 * 60  # learned capacity survives restarts, not weekends
BUCKET_BURST_SECONDS = 2.0  # bucket capacity = pace x this
_ACQUIRE_SLICE_SECONDS = 5.0  # max single sleep — keeps heartbeats responsive

_HTTP_TOO_MANY_REQUESTS = 429

# Budget-header spellings: Atlassian sends X-RateLimit-*, GitLab plain
# RateLimit-*. The X- form wins when both are present.
_RATE_LIMIT_HEADER_PREFIXES = ("X-RateLimit-", "RateLimit-")


class SourceThrottle:
    """One instance per run; implements the http.Throttle protocol."""

    def __init__(self, redis: Redis, *, scope_key: str, base_rate_per_second: float) -> None:
        self._redis = redis
        self._scope = scope_key
        self._base = base_rate_per_second
        self._floor = base_rate_per_second * RATE_FLOOR_FACTOR
        self._ceiling = base_rate_per_second * RATE_CEILING_FACTOR
        self._successes = 0

    @property
    def _rate_key(self) -> str:
        return f"throttle:rate:{self._scope}"

    @property
    def _bucket_key(self) -> str:
        return f"throttle:bucket:{self._scope}"

    @property
    def _pause_key(self) -> str:
        return f"throttle:pause:{self._scope}"

    async def current_rate(self) -> float:
        raw = await self._redis.get(self._rate_key)
        if raw is None:
            return self._base
        try:
            return float(raw)
        except ValueError:  # pragma: no cover — foreign write
            return self._base

    async def _set_rate(self, rate: float) -> None:
        clamped = min(self._ceiling, max(self._floor, rate))
        await self._redis.set(self._rate_key, repr(clamped), ex=RATE_TTL_SECONDS)

    async def acquire(self, cost: int = 1) -> None:
        """Wait for the scope's pause to lift, then debit the bucket."""
        while True:
            pause_ms = await self._redis.pttl(self._pause_key)
            if pause_ms and pause_ms > 0:
                await asyncio.sleep(min(pause_ms / 1000, _ACQUIRE_SLICE_SECONDS))
                continue
            rate = await self.current_rate()
            decision = await hit_token_bucket(
                self._redis,
                self._bucket_key,
                capacity=max(1, round(rate * BUCKET_BURST_SECONDS)),
                refill_per_minute=rate * 60,
                now=time.time(),
                cost=cost,
            )
            if decision.allowed:
                return
            await asyncio.sleep(min(max(decision.retry_after, 0.05), _ACQUIRE_SLICE_SECONDS))

    async def feedback(self, status_code: int, headers: httpx.Headers) -> None:
        if status_code == _HTTP_TOO_MANY_REQUESTS:
            self._successes = 0
            await self._set_rate(await self.current_rate() * AIMD_DECREASE)
            pause = retry_after_seconds(headers)
            if pause:
                await self._redis.set(self._pause_key, "1", px=max(1, int(pause * 1000)))
            return
        if status_code >= 500:
            return  # server trouble teaches nothing about our pace
        safe = _safe_rps(headers)
        if safe is not None:
            await self._set_rate(min(await self.current_rate(), safe))
        self._successes += 1
        if self._successes >= AIMD_WINDOW:
            self._successes = 0
            await self._set_rate(await self.current_rate() * AIMD_INCREASE)


def _budget_header(headers: httpx.Headers, suffix: str) -> str | None:
    """First present spelling of a rate-limit budget header, X- form first."""
    for prefix in _RATE_LIMIT_HEADER_PREFIXES:
        value = headers.get(f"{prefix}{suffix}")
        if value is not None:
            return value
    return None


def _parse_reset(value: str) -> float | None:
    """Reset moment as epoch seconds: GitLab sends epoch, Atlassian ISO-8601."""
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _safe_rps(headers: httpx.Headers) -> float | None:
    """Remaining / (reset - now) when the source volunteers its budget."""
    remaining = _budget_header(headers, "Remaining")
    reset = _budget_header(headers, "Reset")
    if remaining is None or reset is None:
        return None
    try:
        remaining_n = float(remaining)
    except ValueError:
        return None
    reset_at = _parse_reset(reset)
    if reset_at is None:
        return None
    window = reset_at - time.time()
    if window <= 0:
        return None
    return max(0.01, remaining_n / window)
