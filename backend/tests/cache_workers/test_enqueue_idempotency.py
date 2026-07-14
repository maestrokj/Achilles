"""Enqueue is idempotent by job_id — cache-workers tests (integration)."""

import pytest
from redis.asyncio import Redis
from saq import Queue

from achilles.config import Settings
from achilles.infra.worker.base import enqueue_idempotent

pytestmark = [pytest.mark.integration]


async def _noop(ctx: dict[str, object]) -> None:  # pragma: no cover — payload only
    del ctx


@pytest.fixture
async def queue(test_settings: Settings):
    q = Queue.from_url(test_settings.redis_durable_url, name="test-lane")
    await q.connect()
    yield q
    await q.disconnect()


@pytest.fixture
async def raw_redis(test_settings: Settings):
    client = Redis.from_url(test_settings.redis_durable_url, decode_responses=True)
    await client.flushdb()  # type: ignore[misc]
    yield client
    await client.flushdb()  # type: ignore[misc]
    await client.aclose()


async def test_same_job_id_enqueues_once(queue: Queue, raw_redis: Redis):
    first = await enqueue_idempotent(queue, raw_redis, _noop.__name__, job_id="job-42")
    second = await enqueue_idempotent(queue, raw_redis, _noop.__name__, job_id="job-42")
    assert first is True
    assert second is False, "a webhook redelivery or double cron tick must not double work"
    assert await queue.count("queued") == 1


async def test_different_job_ids_both_enqueue(queue: Queue, raw_redis: Redis):
    assert await enqueue_idempotent(queue, raw_redis, _noop.__name__, job_id="job-a")
    assert await enqueue_idempotent(queue, raw_redis, _noop.__name__, job_id="job-b")
    assert await queue.count("queued") == 2
