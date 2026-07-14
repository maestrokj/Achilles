import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from achilles import __version__

if TYPE_CHECKING:
    from achilles.db.connections import DbConnections
    from achilles.infra.redis import RedisPools


class ServiceStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"


class HealthResponse(BaseModel):
    """Aggregated health status of all backing services."""

    status: HealthStatus
    services: dict[str, ServiceStatus]


router = APIRouter(tags=["health"])


async def _probe[T](client: T, check: Callable[[T], Awaitable[object]]) -> ServiceStatus:
    try:
        await check(client)
        return ServiceStatus.OK
    except Exception:
        return ServiceStatus.ERROR


async def ping_postgres(eng: AsyncEngine) -> None:
    async with eng.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def ping_redis(r: Redis) -> None:
    await cast("Awaitable[bool]", r.ping())


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    """Return aggregated health status of all backing services."""
    db: DbConnections = request.state.db
    redis: RedisPools = request.state.redis
    pg, durable, cache = await asyncio.gather(
        _probe(db.pg_engine, ping_postgres),
        _probe(redis.durable, ping_redis),
        _probe(redis.cache, ping_redis),
    )
    services = {"postgres": pg, "redis_durable": durable, "redis_cache": cache}
    ok = all(v == ServiceStatus.OK for v in services.values())
    status = HealthStatus.HEALTHY if ok else HealthStatus.DEGRADED
    return HealthResponse(status=status, services=services)


@router.get("/version")
async def version() -> dict[str, str]:
    """Return current application version."""
    return {"version": __version__}
