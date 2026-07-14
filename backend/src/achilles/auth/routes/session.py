"""Session routes: setup · login · refresh · logout · logout-all.

Design: authentication.html (L3 login, L4 token, L5 session), routing.html#entry-gate.
"""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response, status

from achilles.api.problems import ApiError
from achilles.api.security_headers import SensitiveResponse
from achilles.auth.constants import (
    CODE_SESSION_NOT_FOUND,
    CODE_SETUP_UNAVAILABLE,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    AuditResult,
    UserStatus,
)
from achilles.auth.dependencies import CurrentFamily, CurrentUser
from achilles.auth.models import User
from achilles.auth.routes.common import (
    alert_brute_force,
    client_ip,
    invalid_credentials,
    record_audit,
    redis_durable,
    user_agent,
)
from achilles.auth.schemas import (
    LoginRequest,
    SessionInfo,
    SessionListResponse,
    SessionResponse,
    SetupRequest,
    SetupStatus,
    UserOut,
)
from achilles.auth.security.jwt import issue_access_token
from achilles.auth.security.passwords import (
    dummy_verify_async,
    hash_password_async,
    needs_rehash,
    verify_password_async,
)
from achilles.auth.services import bootstrap, brute_force, sessions
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession

# Every response here may carry tokens — never cached, referrer never leaks.
router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[SensitiveResponse])


def set_refresh_cookie(
    response: Response, token: str, *, remember_me: bool, ttls: sessions.SessionTtls
) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=int(ttls.sliding.total_seconds()) if remember_me else None,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def session_response(
    user: User, *, now: datetime, secret: str, ttls: sessions.SessionTtls
) -> SessionResponse:
    return SessionResponse(
        access_token=issue_access_token(
            user_id=user.id, role=user.role, secret=secret, now=now, ttl=ttls.access
        ),
        must_change_password=user.must_change_password,
        user=UserOut.model_validate(user),
    )


@router.get("/setup")
async def setup_status(session: DbSession) -> SetupStatus:
    """Anonymous first-run probe — the entry gate routes to /setup while true."""
    return SetupStatus(needs_setup=not await bootstrap.users_exist(session))


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    body: SetupRequest, request: Request, response: Response, session: DbSession
) -> SessionResponse:
    """Create the first Owner; disappears (404) as soon as any account exists."""
    if await bootstrap.users_exist(session):
        raise ApiError(404, CODE_SETUP_UNAVAILABLE, "Not Found", "Setup is no longer available")

    now = datetime.now(UTC)
    ttls = await sessions.effective_ttls(session)
    owner = await bootstrap.create_owner(
        session, email=body.email, full_name=body.full_name, password=body.password
    )
    raw_refresh = await sessions.start_session(
        session,
        user=owner,
        now=now,
        ttls=ttls,
        remember_me=False,
        user_agent=user_agent(request),
        ip=client_ip(request),
    )
    await session.commit()

    await record_audit(
        request,
        action=AuditAction.SETUP,
        actor_id=owner.id,
        target_type="user",
        target_id=str(owner.id),
    )
    set_refresh_cookie(response, raw_refresh, remember_me=False, ttls=ttls)
    return session_response(owner, now=now, secret=request.app.state.settings.secret_key, ttls=ttls)


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, session: DbSession
) -> SessionResponse:
    now = datetime.now(UTC)
    redis = redis_durable(request)
    ip = client_ip(request)
    email = body.email.lower()

    if ip:
        await brute_force.check_ip(redis, ip, now=now)
    await brute_force.check_account_delay(redis, email, now=now)

    user = await session.scalar(sa.select(User).where(sa.func.lower(User.email) == email))

    async def fail() -> ApiError:
        count = await brute_force.record_failure(redis, email, now=now)
        await alert_brute_force(request, email=email, count=count, now=now)
        await record_audit(
            request,
            action=AuditAction.LOGIN,
            result=AuditResult.FAILURE,
            meta={"email": email},
        )
        return invalid_credentials()

    if user is None or user.password_hash is None:
        await dummy_verify_async()  # timing stays flat for unknown emails
        raise await fail()
    if not await verify_password_async(user.password_hash, body.password):
        raise await fail()
    if user.status != UserStatus.ACTIVE.value:
        raise await fail()

    await brute_force.reset(redis, email)
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        # Params moved on (or a legacy hash) — re-derive transparently while we hold the password.
        user.password_hash = await hash_password_async(body.password)
    ttls = await sessions.effective_ttls(session)
    raw_refresh = await sessions.start_session(
        session,
        user=user,
        now=now,
        ttls=ttls,
        remember_me=body.remember_me,
        user_agent=user_agent(request),
        ip=ip,
    )
    await session.commit()

    await record_audit(request, action=AuditAction.LOGIN, actor_id=user.id)
    set_refresh_cookie(response, raw_refresh, remember_me=body.remember_me, ttls=ttls)
    return session_response(user, now=now, secret=request.app.state.settings.secret_key, ttls=ttls)


@router.post("/refresh")
async def refresh(request: Request, response: Response, session: DbSession) -> SessionResponse:
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_token:
        raise sessions.token_invalid()

    now = datetime.now(UTC)
    ttls = await sessions.effective_ttls(session)
    try:
        result = await sessions.rotate(
            session,
            redis_durable(request),
            raw_token=raw_token,
            now=now,
            ttls=ttls,
            user_agent=user_agent(request),
            ip=client_ip(request),
        )
    except ApiError as exc:
        await session.commit()  # persist the family kill from reuse detection
        if isinstance(exc, sessions.ReuseDetectedError):
            # A replayed token is the one theft signal this endpoint sees — journal it.
            await record_audit(
                request,
                action=AuditAction.REFRESH_REUSE,
                result=AuditResult.FAILURE,
                actor_id=exc.user_id,
                meta={"family_id": str(exc.family_id)},
            )
        raise
    await session.commit()

    set_refresh_cookie(
        response, result.raw_refresh_token, remember_me=result.remember_me, ttls=ttls
    )
    return session_response(
        result.user, now=now, secret=request.app.state.settings.secret_key, ttls=ttls
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: DbSession) -> None:
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_token:
        raise sessions.token_invalid()

    row = await sessions.end_session(session, raw_token=raw_token)
    await session.commit()
    if row is not None:
        await record_audit(request, action=AuditAction.LOGOUT, actor_id=row.user_id)
    clear_refresh_cookie(response)
    response.headers["Clear-Site-Data"] = '"cache", "storage"'


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    user: CurrentUser, request: Request, response: Response, session: DbSession
) -> None:
    """Terminate every session of the current user (all devices)."""
    count = await sessions.end_all_sessions(session, user_id=user.id)
    await session.commit()
    await record_audit(
        request, action=AuditAction.LOGOUT_ALL, actor_id=user.id, meta={"sessions": count}
    )
    clear_refresh_cookie(response)


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser, current: CurrentFamily, session: DbSession
) -> SessionListResponse:
    """The user's live device sessions; the one behind the cookie is flagged current."""
    now = datetime.now(UTC)
    active = await sessions.list_active_sessions(session, user_id=user.id, now=now, current=current)
    return SessionListResponse(
        items=[
            SessionInfo(
                id=row.family_id,
                user_agent=row.user_agent,
                ip=row.ip,
                created_at=row.created_at,
                is_current=row.is_current,
            )
            for row in active
        ]
    )


@router.delete("/sessions/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    family_id: UUID,
    user: CurrentUser,
    current: CurrentFamily,
    request: Request,
    response: Response,
    session: DbSession,
) -> None:
    """Sign out one device (a token family). Revoking the current one clears the cookie."""
    count = await sessions.revoke_family(session, user_id=user.id, family_id=family_id)
    await session.commit()
    if count == 0:
        raise ApiError(404, CODE_SESSION_NOT_FOUND, "Not Found", "No such session")
    await record_audit(
        request,
        action=AuditAction.SESSION_REVOKE,
        actor_id=user.id,
        target_type="session",
        target_id=str(family_id),
    )
    if family_id == current:
        clear_refresh_cookie(response)


@router.post("/sessions/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(
    user: CurrentUser, current: CurrentFamily, request: Request, session: DbSession
) -> None:
    """End every session except the current device (keeps the caller signed in)."""
    count = await sessions.revoke_other_families(session, user_id=user.id, keep=current)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SESSIONS_REVOKE_OTHERS,
        actor_id=user.id,
        meta={"sessions": count},
    )
