"""Org maintenance mode — the Owner's switch (platform-settings.html#maintenance).

Distinct from the restore lock (knowledge_store/services/maintenance.py): that
one is claimed by a job and pauses retrieval for everyone; this one is a manual
platform pause. platform_settings.maintenance_mode is the source of truth; the
PATCH mirrors it into redis-durable so the request path checks one flag with no
DB read. Members get 503 + Retry-After; Owner/Admin pass (someone must be able
to switch it back off); anonymous login/refresh stay open for the same reason.
The flag carries no TTL — only an explicit PATCH clears it.
"""

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.constants import UserRole
from achilles.auth.models import User
from achilles.infra.redis import PREFIX_LOCK
from achilles.knowledge_store.constants import CODE_MAINTENANCE
from achilles.knowledge_store.services import platform

MAINTENANCE_ADMIN_KEY = f"{PREFIX_LOCK}maintenance_admin"
MAINTENANCE_RETRY_AFTER_SECONDS = 600


async def set_enabled(redis: Redis, *, enabled: bool) -> None:
    if enabled:
        await redis.set(MAINTENANCE_ADMIN_KEY, "1")
    else:
        await redis.delete(MAINTENANCE_ADMIN_KEY)


async def is_enabled(redis: Redis) -> bool:
    return bool(await redis.exists(MAINTENANCE_ADMIN_KEY))


async def sync_from_db(session: AsyncSession, redis: Redis) -> None:
    """Lifespan heal: the DB row survives a redis wipe, so it re-seeds the mirror."""
    row = await platform.get_platform_settings(session)
    await set_enabled(redis, enabled=row.maintenance_mode)


def maintenance_error() -> ApiError:
    return ApiError(
        503,
        CODE_MAINTENANCE,
        "Maintenance in progress",
        "The platform is in maintenance mode; retry later.",
        retry_after=MAINTENANCE_RETRY_AFTER_SECONDS,
    )


async def ensure_member_access(request: Request, user: User) -> None:
    """The request-path gate: members wait out maintenance, Owner/Admin pass."""
    if user.role != UserRole.MEMBER.value:
        return
    if await is_enabled(request.state.redis.durable):
        raise maintenance_error()
