"""Invite routes: list · single · bulk CSV · resend · revoke · accept."""

from datetime import UTC, datetime
from typing import Annotated, Literal, TypedDict

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr

from achilles.api.pagination import OffsetPage, OffsetParams, offset_page
from achilles.api.problems import CODE_FORBIDDEN, ApiError
from achilles.api.security_headers import SensitiveResponse
from achilles.api.serialization import UtcDateTime
from achilles.auth.constants import CODE_EMAIL_TAKEN, Permission, UserRole
from achilles.auth.dependencies import require
from achilles.auth.models import InviteToken, User
from achilles.auth.routes.common import client_ip, record_audit, user_agent
from achilles.auth.routes.session import session_response, set_refresh_cookie
from achilles.auth.schemas import SessionResponse
from achilles.auth.services import invites, sessions
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.email import service as email_service
from achilles.email.api import queue_invite
from achilles.infra.worker.base import Lane

router = APIRouter(prefix="/invites", tags=["invites"])

Inviter = Annotated[User, require(Permission.USERS_INVITE)]

type InviteStatus = Literal["pending", "accepted", "expired"]


class InviteCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.MEMBER


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    expires_at: UtcDateTime
    created_at: UtcDateTime


class InviteListItem(InviteOut):
    status: InviteStatus


def _invite_status(row: InviteToken, now: datetime) -> InviteStatus:
    if row.accepted_at is not None:
        return "accepted"
    return "pending" if row.expires_at > now else "expired"


class InviteAccept(BaseModel):
    full_name: str
    password: str


class _RowRole(TypedDict):
    role: str
    role_from_default: bool


class BulkRowOut(BaseModel):
    row: int
    email: str
    status: str
    message: str = ""
    # The role the invite will carry, plus whether it came from `default_role`
    # (no role column of its own) — the preview marks those so the admin sees
    # which rows the default-role selector governs.
    role: str = ""
    role_from_default: bool = False


class BulkReport(BaseModel):
    results: list[BulkRowOut]


@router.get("")
async def list_invites(
    actor: Inviter,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    invite_status: Annotated[list[InviteStatus] | None, Query(alias="status")] = None,
    role: Annotated[list[UserRole] | None, Query()] = None,
) -> OffsetPage[InviteListItem]:
    """The invites tab: search by email, Status/Role facets, newest first."""
    del actor
    now = datetime.now(UTC)
    stmt = sa.select(InviteToken).order_by(InviteToken.id.desc())
    if q:
        stmt = stmt.where(InviteToken.email.icontains(q.strip(), autoescape=True))
    if role:
        stmt = stmt.where(InviteToken.role.in_([r.value for r in role]))
    if invite_status:
        by_status = {
            "accepted": InviteToken.accepted_at.is_not(None),
            "pending": sa.and_(InviteToken.accepted_at.is_(None), InviteToken.expires_at > now),
            "expired": sa.and_(InviteToken.accepted_at.is_(None), InviteToken.expires_at <= now),
        }
        stmt = stmt.where(sa.or_(*[by_status[s] for s in invite_status]))

    rows, total, page = await offset_page(session, stmt, params)
    return OffsetPage(
        items=[
            InviteListItem(
                **InviteOut.model_validate(r).model_dump(), status=_invite_status(r, now)
            )
            for r in rows
        ],
        total=total,
        page=page,
        per_page=params.per_page,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreate,
    actor: Inviter,
    request: Request,
    session: DbSession,
) -> InviteOut:
    if not await email_service.smtp_available(session):
        raise invites.smtp_not_configured()
    now = datetime.now(UTC)
    raw, row = await invites.create_invite(
        session, actor=actor, email=body.email, role=body.role.value, now=now
    )
    await session.commit()
    await queue_invite(
        request, lane=Lane.INTERACTIVE, raw=raw, row=row, inviter_name=actor.full_name
    )
    await record_audit(
        request,
        action=AuditAction.INVITE_CREATE,
        actor_id=actor.id,
        target_type="invite",
        target_id=str(row.id),
        meta={"role": row.role},
    )
    return InviteOut.model_validate(row)


@router.post("/bulk", status_code=status.HTTP_207_MULTI_STATUS)
async def bulk_invite(
    actor: Inviter,
    request: Request,
    session: DbSession,
    file: Annotated[bytes, File()],
    dry_run: bool = False,  # noqa: FBT001, FBT002 — a query-string switch, not a code trap
    default_role: UserRole = UserRole.MEMBER,
) -> BulkReport:
    """CSV `email[,role]`; per-row report, valid rows are not rolled back by invalid ones.

    Letters go to the background lane one job per row — the worker paces bulk
    volume, the report answers on admission, not delivery (delivery.html#queue).
    `dry_run` runs the same per-row classification but persists nothing:
    "created" reads as "will be created". `default_role` fills rows without a
    role column of their own.
    """
    if not await email_service.smtp_available(session):
        raise invites.smtp_not_configured()
    now = datetime.now(UTC)
    results: list[BulkRowOut] = []
    seen: set[str] = set()
    created: list[tuple[str, InviteToken]] = []

    for row_no, email, role, from_default in invites.parse_bulk_csv(
        file, default_role=default_role.value
    ):
        row_role: _RowRole = {"role": role, "role_from_default": from_default}
        if "@" not in email:
            results.append(
                BulkRowOut(row=row_no, email=email, status="invalid", message="email", **row_role)
            )
            continue
        if role not in {r.value for r in UserRole}:
            results.append(
                BulkRowOut(row=row_no, email=email, status="invalid", message="role", **row_role)
            )
            continue
        if email in seen:
            results.append(BulkRowOut(row=row_no, email=email, status="duplicate", **row_role))
            continue
        seen.add(email)
        try:
            raw, invite_row = await invites.create_invite(
                session, actor=actor, email=email, role=role, now=now
            )
        except ApiError as exc:
            # A stable message token, not the raw English detail — the frontend
            # localizes it. A forbidden role is its own case: the email is fine,
            # the actor just can't grant that role, so it must not read as a
            # "already exists" conflict.
            if exc.code == CODE_EMAIL_TAKEN:
                status, message = "conflict", ""
            elif exc.code == CODE_FORBIDDEN:
                status, message = "invalid", "role_forbidden"
            else:
                status, message = "invalid", "error"
            results.append(
                BulkRowOut(row=row_no, email=email, status=status, message=message, **row_role)
            )
            continue
        created.append((raw, invite_row))
        results.append(BulkRowOut(row=row_no, email=email, status="created", **row_role))

    if dry_run:
        # Same classification, zero side effects: no rows, no letters, no audit.
        await session.rollback()
        return BulkReport(results=results)

    await session.commit()
    for raw, invite_row in created:
        await queue_invite(
            request, lane=Lane.BACKGROUND, raw=raw, row=invite_row, inviter_name=actor.full_name
        )
    await record_audit(
        request,
        action=AuditAction.INVITE_CREATE,
        actor_id=actor.id,
        target_type="invite",
        target_id="bulk",
        meta={"rows": len(results)},
    )
    return BulkReport(results=results)


@router.post("/{invite_id}/resend", status_code=status.HTTP_201_CREATED)
async def resend_invite(
    invite_id: int,
    actor: Inviter,
    request: Request,
    session: DbSession,
) -> InviteOut:
    """A fresh link on the same invite; the old one stops working at once."""
    if not await email_service.smtp_available(session):
        raise invites.smtp_not_configured()
    raw, row = await invites.resend_invite(
        session, actor=actor, invite_id=invite_id, now=datetime.now(UTC)
    )
    await session.commit()
    await queue_invite(
        request, lane=Lane.INTERACTIVE, raw=raw, row=row, inviter_name=actor.full_name
    )
    await record_audit(
        request,
        action=AuditAction.INVITE_RESEND,
        actor_id=actor.id,
        target_type="invite",
        target_id=str(row.id),
    )
    return InviteOut.model_validate(row)


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: int, actor: Inviter, request: Request, session: DbSession
) -> None:
    row = await invites.revoke_invite(session, actor=actor, invite_id=invite_id)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.INVITE_REVOKE,
        actor_id=actor.id,
        target_type="invite",
        target_id=str(invite_id),
        meta={"email": row.email},
    )


# The response carries session tokens — marked sensitive like the /auth routes.
@router.post(
    "/{token}/accept", status_code=status.HTTP_201_CREATED, dependencies=[SensitiveResponse]
)
async def accept_invite(
    token: str,
    body: InviteAccept,
    request: Request,
    response: Response,
    session: DbSession,
) -> SessionResponse:
    """Registration by invite: name + password → account + a fresh session."""
    now = datetime.now(UTC)
    ttls = await sessions.effective_ttls(session)
    user = await invites.accept_invite(
        session, token, full_name=body.full_name, password=body.password, now=now
    )
    raw_refresh = await sessions.start_session(
        session,
        user=user,
        now=now,
        ttls=ttls,
        remember_me=False,
        user_agent=user_agent(request),
        ip=client_ip(request),
    )
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.INVITE_ACCEPT,
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
    )
    set_refresh_cookie(response, raw_refresh, remember_me=False, ttls=ttls)
    return session_response(user, now=now, secret=request.app.state.settings.secret_key, ttls=ttls)
