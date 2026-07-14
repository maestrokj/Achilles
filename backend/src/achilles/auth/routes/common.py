"""Shared request-scope helpers for the auth routers."""

import logging
from datetime import datetime
from typing import Any

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.api.problems import ApiError
from achilles.auth.constants import CODE_INVALID_CREDENTIALS, AuditResult
from achilles.auth.services import audit, brute_force
from achilles.auth.services.audit import AuditAction
from achilles.notifications.api import enqueue_event

logger = logging.getLogger(__name__)


def redis_durable(request: Request) -> Redis:
    return request.state.redis.durable


def session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.state.db.pg_session_factory


def client_ip(request: Request) -> str | None:
    # uvicorn --proxy-headers resolves X-Forwarded-For from trusted proxies;
    # the app itself never parses the header.
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("User-Agent")


async def record_audit(
    request: Request,
    *,
    action: AuditAction,
    result: AuditResult = AuditResult.SUCCESS,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Request-bound audit entry: session factory, ip and user-agent are derived here."""
    await audit.record(
        session_factory(request),
        action=action,
        result=result,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        ip=client_ip(request),
        user_agent=user_agent(request),
        meta=meta,
    )


def invalid_credentials() -> ApiError:
    # Always generic: unknown email, wrong password and deactivated look identical.
    return ApiError(401, CODE_INVALID_CREDENTIALS, "Unauthorized", "Invalid credentials")


async def alert_brute_force(request: Request, *, email: str, count: int, now: datetime) -> None:
    """At the alert threshold, raise the security event off the request path.

    The Redis-pipeline context of record_failure has no queue plumbing, so the
    route enqueues; the alert must never break the login flow itself.
    """
    if not brute_force.alert_due(count):
        return
    email_hash = brute_force.hash_email(email)
    await enqueue_event(
        request,
        event="security.brute_force",
        params={"email": email},
        dedup_key=f"brute:{email_hash}",
        job_key=f"brute:{email_hash}:{int(now.timestamp())}",
    )
