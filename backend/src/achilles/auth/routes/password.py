"""Password routes: change · forgot · reset."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, EmailStr

from achilles.api.security_headers import SensitiveResponse
from achilles.auth.constants import (
    FORGOT_IP_LIMIT,
    FORGOT_IP_WINDOW,
    FORGOT_RESEND_LIMIT,
    FORGOT_RESEND_WINDOW,
    REFRESH_COOKIE_NAME,
    AuditResult,
)
from achilles.auth.dependencies import CurrentUser
from achilles.auth.routes.common import (
    alert_brute_force,
    client_ip,
    invalid_credentials,
    record_audit,
    redis_durable,
)
from achilles.auth.security.passwords import verify_password_async
from achilles.auth.security.tokens import hash_token
from achilles.auth.services import brute_force, passwords
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.email.api import queue_password_reset
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import PREFIX_RATE_LIMIT

router = APIRouter(prefix="/auth/password", tags=["auth"], dependencies=[SensitiveResponse])

_FORGOT_KEY = PREFIX_RATE_LIMIT + "forgot:{email_hash}"
_FORGOT_IP_KEY = PREFIX_RATE_LIMIT + "forgot-ip:{ip}"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class StatusResponse(BaseModel):
    status: str = "ok"


@router.post("/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, user: CurrentUser, request: Request, session: DbSession
) -> None:
    now = datetime.now(UTC)
    redis = redis_durable(request)
    email = user.email.lower()

    # A wrong `current` feeds the same per-account barrier as login failures.
    await brute_force.check_account_delay(redis, email, now=now)
    if user.password_hash is None or not await verify_password_async(
        user.password_hash, body.current_password
    ):
        count = await brute_force.record_failure(redis, email, now=now)
        await alert_brute_force(request, email=email, count=count, now=now)
        await record_audit(
            request,
            action=AuditAction.PASSWORD_CHANGE,
            result=AuditResult.FAILURE,
            actor_id=user.id,
        )
        raise invalid_credentials()
    if body.new_password == body.current_password:
        raise passwords.same_as_current_422()

    raw_cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    await passwords.apply_new_password(
        session,
        user,
        body.new_password,
        keep_token_hash=hash_token(raw_cookie) if raw_cookie else None,
    )
    await session.commit()
    await brute_force.reset(redis, email)
    await record_audit(request, action=AuditAction.PASSWORD_CHANGE, actor_id=user.id)


@router.post("/forgot")
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, session: DbSession
) -> StatusResponse:
    """Anti-enumeration: the answer is identical whether the account exists or not.

    The whole flow (account lookup → token issue → letter) runs in the worker —
    the request path does the same work for every input, so timing cannot tell
    an existing account from a missing one. Delivery failures stay in log/audit.
    """
    del session  # the uniform answer needs no DB — the job owns the lookup
    now = datetime.now(UTC)
    email = body.email.lower()
    email_hash = brute_force.hash_email(email)

    # IP first: the per-email window alone is bypassed by unique addresses.
    ip_decision = await hit_sliding_window(
        redis_durable(request),
        _FORGOT_IP_KEY.format(ip=client_ip(request) or "unknown"),
        limit=FORGOT_IP_LIMIT,
        window_seconds=int(FORGOT_IP_WINDOW.total_seconds()),
        now=now.timestamp(),
    )
    if not ip_decision.allowed:
        raise brute_force.rate_limited(ip_decision.retry_after)

    decision = await hit_sliding_window(
        redis_durable(request),
        _FORGOT_KEY.format(email_hash=email_hash),
        limit=FORGOT_RESEND_LIMIT,
        window_seconds=int(FORGOT_RESEND_WINDOW.total_seconds()),
        now=now.timestamp(),
    )
    if not decision.allowed:
        raise brute_force.rate_limited(decision.retry_after)

    await queue_password_reset(request, email=email)
    return StatusResponse()


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, request: Request, session: DbSession) -> None:
    now = datetime.now(UTC)
    user = await passwords.consume_reset_token(session, body.token, now=now)
    await passwords.apply_new_password(session, user, body.new_password)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.PASSWORD_RESET,
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
