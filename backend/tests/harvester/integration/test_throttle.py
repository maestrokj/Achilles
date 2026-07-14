"""SourceThrottle: AIMD learning, pause park, bucket cost (integration, redis)."""

import time
from collections.abc import AsyncGenerator

import httpx
import pytest
from redis.asyncio import Redis

from achilles.config import Settings
from achilles.harvester.pipeline.throttle import (
    AIMD_WINDOW,
    RATE_CEILING_FACTOR,
    RATE_FLOOR_FACTOR,
    SourceThrottle,
)

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BASE_RATE = 2.0


@pytest.fixture
async def redis(test_settings: Settings) -> AsyncGenerator[Redis]:
    client = Redis.from_url(test_settings.redis_durable_url, decode_responses=True)
    yield client
    await client.aclose()


def _throttle(redis: Redis, scope: str = "test:scope") -> SourceThrottle:
    return SourceThrottle(redis, scope_key=scope, base_rate_per_second=BASE_RATE)


async def test_rate_grows_after_success_window(redis: Redis) -> None:
    throttle = _throttle(redis)
    for _ in range(AIMD_WINDOW):
        await throttle.feedback(200, httpx.Headers({}))
    assert await throttle.current_rate() == pytest.approx(BASE_RATE * 1.1)


async def test_429_halves_rate_with_floor(redis: Redis) -> None:
    throttle = _throttle(redis)
    for _ in range(10):
        await throttle.feedback(429, httpx.Headers({}))
    assert await throttle.current_rate() == pytest.approx(BASE_RATE * RATE_FLOOR_FACTOR)


async def test_rate_never_exceeds_ceiling(redis: Redis) -> None:
    throttle = _throttle(redis)
    for _ in range(AIMD_WINDOW * 40):
        await throttle.feedback(200, httpx.Headers({}))
    assert await throttle.current_rate() == pytest.approx(BASE_RATE * RATE_CEILING_FACTOR)


async def test_retry_after_parks_the_scope(redis: Redis) -> None:
    throttle = _throttle(redis)
    await throttle.feedback(429, httpx.Headers({"Retry-After": "30"}))
    pause_ms = await redis.pttl("throttle:pause:test:scope")
    assert pause_ms > 0  # acquire() on any worker sleeps this out first


async def test_learned_rate_survives_new_instance(redis: Redis) -> None:
    first = _throttle(redis)
    for _ in range(10):
        await first.feedback(429, httpx.Headers({}))
    slowed = await first.current_rate()

    second = _throttle(redis)  # "worker restart"
    assert await second.current_rate() == pytest.approx(slowed)


async def test_header_budget_clamps_rate(redis: Redis) -> None:
    throttle = _throttle(redis)
    # 10 requests left in a ~100 s window → ~0.1 rps, well under the base pace.
    headers = httpx.Headers(
        {"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": str(int(time.time()) + 100)}
    )
    await throttle.feedback(200, headers)
    assert await throttle.current_rate() < BASE_RATE


async def test_acquire_debits_and_flows_when_tokens_available(redis: Redis) -> None:
    throttle = _throttle(redis)
    await throttle.acquire()  # fresh bucket at capacity — returns without waiting


async def test_server_errors_teach_nothing(redis: Redis) -> None:
    throttle = _throttle(redis)
    for _ in range(AIMD_WINDOW * 2):
        await throttle.feedback(503, httpx.Headers({}))
    assert await throttle.current_rate() == pytest.approx(BASE_RATE)
