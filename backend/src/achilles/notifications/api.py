"""The raise-event faces: request path, worker path, sessionless enqueue.

The invariant they all enforce lives here once: a notification must never
fail the work that raised it.
"""

import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.api.background import publish_lane
from achilles.config import settings as app_settings
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.infra.worker.base import Lane
from achilles.notifications.dispatcher import DispatchResult, dispatch_and_publish

logger = logging.getLogger(__name__)


async def dispatch_from_request(
    request: Request,
    session: AsyncSession,
    *,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
) -> DispatchResult | None:
    """Raise one event from a route; a notification must never fail the request."""
    try:
        return await dispatch_and_publish(
            session,
            request.state.redis,
            queue_url=request.app.state.settings.redis_durable_url,
            event=event,
            target_user_id=target_user_id,
            source_ref=source_ref,
            params=params,
            dedup_key=dedup_key,
        )
    except Exception:
        logger.warning("notification dispatch for %s failed", event, exc_info=True)
        await session.rollback()  # leave the route a clean transaction
        return None


async def dispatch_from_tick(
    session: AsyncSession,
    redis: RedisPools,
    *,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
) -> None:
    """Raise one event mid-loop on a shared session; a failure must not poison it.

    The rollback matters: a swallowed flush/commit error would otherwise leave
    the caller's session pending-rollback and crash the rest of its loop.
    """
    try:
        await dispatch_and_publish(
            session,
            redis,
            queue_url=app_settings.redis_durable_url,
            event=event,
            target_user_id=target_user_id,
            source_ref=source_ref,
            params=params,
            dedup_key=dedup_key,
        )
    except Exception:
        logger.warning("notification dispatch for %s failed", event, exc_info=True)
        await session.rollback()


async def dispatch_from_worker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: RedisPools | None = None,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
) -> None:
    """Raise one event from a worker job; a notification must never fail the job.

    Owns the pool lifecycle when the caller has none open, the queue URL and
    the swallow-and-warn policy — call sites keep only their domain lookup.
    """
    pools = redis or create_redis_pools(app_settings)
    try:
        async with session_factory() as session:
            await dispatch_and_publish(
                session,
                pools,
                queue_url=app_settings.redis_durable_url,
                event=event,
                target_user_id=target_user_id,
                source_ref=source_ref,
                params=params,
                dedup_key=dedup_key,
            )
    except Exception:
        logger.warning("notification dispatch for %s failed", event, exc_info=True)
    finally:
        if redis is None:
            await close_redis_pools(pools)


async def enqueue_event(
    request: Request,
    *,
    event: str,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
    job_key: str,
) -> None:
    """Sessionless contexts enqueue `raise_event` — the job-name/lane/id contract lives here."""
    try:
        await publish_lane(
            request,
            Lane.INTERACTIVE,
            "raise_event",
            job_id=f"nraise:{job_key}",
            event=event,
            params=params,
            dedup_key=dedup_key,
        )
    except Exception:
        logger.warning("notification enqueue for %s failed", event, exc_info=True)
