"""Exact cache at the RAG route entrance (rag-pipeline.html#cache).

Key = standalone query + identity — never shared across users (the result is
ACL-shaped). Exact, not semantic: a hit skips embed + retrieve entirely.
Fail-open on both sides: a silent Redis costs a cache miss, never the turn.
"""

import hashlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from achilles.infra.redis import PREFIX_CACHE
from achilles.query_engine.constants import RAG_CACHE_TTL

logger = logging.getLogger(__name__)

# Bump when the cached payload shape changes (search.py builds it): a new
# generation of keys makes stale entries misses instead of TypeErrors during
# the TTL window right after a deploy.
PAYLOAD_SCHEMA_VERSION = 1

_KEY = PREFIX_CACHE + "rag:v{version}:{user_id}:{digest}"


def _key(user_id: int, query: str) -> str:
    digest = hashlib.sha256(" ".join(query.lower().split()).encode()).hexdigest()
    return _KEY.format(version=PAYLOAD_SCHEMA_VERSION, user_id=user_id, digest=digest)


async def get(cache: Redis, *, user_id: int, query: str) -> dict[str, object] | None:
    try:
        raw = await cache.get(_key(user_id, query))
    except RedisError as exc:
        logger.warning("rag cache read failed: %s", exc)
        return None
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def put(cache: Redis, *, user_id: int, query: str, payload: dict[str, object]) -> None:
    try:
        await cache.set(_key(user_id, query), json.dumps(payload), ex=RAG_CACHE_TTL)
    except RedisError as exc:
        logger.warning("rag cache write failed: %s", exc)
