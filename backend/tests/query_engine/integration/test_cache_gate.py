"""Exact RAG cache: per-identity keys, normalization, fail-open (P1)."""

import pytest
from redis.asyncio import Redis

from achilles.config import Settings
from achilles.query_engine.rag import cache_gate

pytestmark = [pytest.mark.integration, pytest.mark.p1]

PAYLOAD: dict[str, object] = {"candidates": [], "evidence": [], "degraded": False, "hidden": None}


@pytest.fixture
async def cache(test_settings: Settings):
    redis = Redis.from_url(test_settings.redis_cache_url)
    yield redis
    await redis.aclose()


async def test_roundtrip_and_whitespace_case_normalization(cache: Redis):
    await cache_gate.put(cache, user_id=1, query="How  Do We DEPLOY?", payload=PAYLOAD)
    assert await cache_gate.get(cache, user_id=1, query="how do we deploy?") == PAYLOAD


async def test_identity_is_part_of_the_key(cache: Redis):
    """The cached result is ACL-shaped — another user must never see it."""
    await cache_gate.put(cache, user_id=1, query="secret question", payload=PAYLOAD)
    assert await cache_gate.get(cache, user_id=2, query="secret question") is None


async def test_silent_redis_is_a_miss_not_an_error(test_settings: Settings):
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.05)
    assert await cache_gate.get(dead, user_id=1, query="q") is None
    await cache_gate.put(dead, user_id=1, query="q", payload=PAYLOAD)  # must not raise
    await dead.aclose()


async def test_garbage_in_the_slot_is_a_miss(cache: Redis):
    await cache_gate.put(cache, user_id=1, query="q", payload=PAYLOAD)
    # Overwrite with non-JSON garbage under the same key namespace.
    keys = [key async for key in cache.scan_iter(match="cache:rag:*")]
    assert keys
    await cache.set(keys[0], b"not json")
    assert await cache_gate.get(cache, user_id=1, query="q") is None
