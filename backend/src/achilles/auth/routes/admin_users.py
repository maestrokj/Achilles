"""Admin user management + audit-log read.

Design: users.html, user-card.html (contract), authorization.html scope rules.
"""

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr

from achilles.api.pagination import OffsetPage, OffsetParams, offset_page, offset_window
from achilles.api.problems import field_validation_error
from achilles.api.security_headers import SensitiveResponse
from achilles.api.serialization import UtcDateTime
from achilles.auth.constants import (
    Permission,
    UserRole,
    UserStatus,
)
from achilles.auth.dependencies import require
from achilles.auth.models import AuditLog, RefreshToken, User
from achilles.auth.routes.common import record_audit
from achilles.auth.schemas import UserOut
from achilles.auth.services import sessions as sessions_service
from achilles.auth.services import users_admin
from achilles.auth.services.audit import AUDIT_ACTION_GROUPS, AuditAction
from achilles.auth.services.users_admin import forbidden, user_search_clause
from achilles.db.dependencies import DbSession
from achilles.email import service as email_service
from achilles.email.api import queue_password_reset
from achilles.notifications.api import dispatch_from_request

router = APIRouter(prefix="/admin", tags=["admin"])

ManageUsers = Annotated[User, require(Permission.USERS_MANAGE)]
ReadAudit = Annotated[User, require(Permission.AUDIT_READ)]

# The "last login" facet windows (users.html): a window name → max age.
LAST_LOGIN_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
LAST_LOGIN_NEVER = "never"

# Columns of the users export (users.html, legend 1) — the visible list plus the
# stable id and the join date. UserOut carries no secrets, so nothing to strip.
EXPORT_COLUMNS: tuple[str, ...] = (
    "id",
    "email",
    "full_name",
    "role",
    "status",
    "last_login_at",
    "created_at",
)

# Spreadsheet formula triggers: a cell opening with one of these is evaluated by
# Excel/Sheets/LibreOffice on load. A user controls their own full_name, so the
# export is a stored-injection sink — prefix such cells with ' to neutralize them
# (CWE-1236). JSON export is inert and left verbatim.
_CSV_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_LEADERS):
        return "'" + value
    return value


def filtered_users_stmt(
    q: str | None,
    role: list[UserRole] | None,
    status_: list[UserStatus] | None,
    last_login: str | None,
) -> sa.Select[tuple[User]]:
    """The list tab's WHERE, shared by the paged list and the export.

    Search + Role/Status/Last-login facets; ordered by name then id.
    """
    stmt = sa.select(User).order_by(User.full_name, User.id)
    if q:
        stmt = stmt.where(user_search_clause(q.strip()))
    if role:
        stmt = stmt.where(User.role.in_([r.value for r in role]))
    if status_:
        stmt = stmt.where(User.status.in_([s.value for s in status_]))
    if last_login is not None:
        if last_login == LAST_LOGIN_NEVER:
            stmt = stmt.where(User.last_login_at.is_(None))
        elif last_login in LAST_LOGIN_WINDOWS:
            floor = datetime.now(UTC) - LAST_LOGIN_WINDOWS[last_login]
            stmt = stmt.where(User.last_login_at >= floor)
        else:
            raise field_validation_error("last_login", "24h | 7d | 30d | never")
    return stmt


class AdminUserPatch(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    role: UserRole | None = None
    status: UserStatus | None = None


class AdminUserDetail(UserOut):
    active_sessions: int


class AdminResetOut(BaseModel):
    """`link`: a 1h reset letter was queued; `temp_password`: the SMTP-less fallback."""

    mode: Literal["link", "temp_password"]
    temp_password: str | None = None


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int | None
    actor_email: str | None = None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    ip: str | None
    user_agent: str | None
    meta: dict[str, Any] | None
    created_at: UtcDateTime


class AuditLogPage(OffsetPage[AuditLogOut]):
    groups: list[str]
    """The action-group catalogue — the frontend renders the filter, never lists."""


@router.get("/users")
async def list_users(
    actor: ManageUsers,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    role: Annotated[list[UserRole] | None, Query()] = None,
    status_: Annotated[list[UserStatus] | None, Query(alias="status")] = None,
    last_login: str | None = None,
) -> OffsetPage[UserOut]:
    """The list tab: server-side search + Role/Status/Last-login facets."""
    del actor
    stmt = filtered_users_stmt(q, role, status_, last_login)
    rows, total, page = await offset_page(session, stmt, params)
    return OffsetPage(
        items=[UserOut.model_validate(u) for u in rows],
        total=total,
        page=page,
        per_page=params.per_page,
    )


@router.get("/users/export", dependencies=[SensitiveResponse])
async def export_users(
    actor: ManageUsers,
    request: Request,
    session: DbSession,
    format: Literal["csv", "json"] = "csv",
    q: str | None = None,
    role: Annotated[list[UserRole] | None, Query()] = None,
    status_: Annotated[list[UserStatus] | None, Query(alias="status")] = None,
    last_login: str | None = None,
) -> Response:
    """Download the current list (respecting search + facets) as CSV or JSON.

    A bulk PII read, so it is audited. Declared before ``/users/{user_id}`` —
    otherwise "export" would be matched as a (non-int) user id.
    """
    stmt = filtered_users_stmt(q, role, status_, last_login)
    rows = (await session.scalars(stmt)).all()
    records = [
        UserOut.model_validate(u).model_dump(mode="json", include=set(EXPORT_COLUMNS)) for u in rows
    ]

    if format == "json":
        body = json.dumps(records, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows({k: _csv_safe(v) for k, v in record.items()} for record in records)
        body = buffer.getvalue()
        media_type = "text/csv"

    await record_audit(
        request,
        action=AuditAction.USER_EXPORT,
        actor_id=actor.id,
        meta={"format": format, "count": len(rows)},
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="users.{format}"'},
    )


@router.get("/users/{user_id}")
async def get_user(user_id: int, actor: ManageUsers, session: DbSession) -> AdminUserDetail:
    del actor
    target = await users_admin.get_user_or_404(session, user_id)
    now = datetime.now(UTC)
    active_sessions = (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == target.id,
                sa.not_(RefreshToken.is_revoked),
                RefreshToken.expires_at > now,
            )
        )
        or 0
    )
    return AdminUserDetail(
        **UserOut.model_validate(target).model_dump(), active_sessions=active_sessions
    )


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: int,
    body: AdminUserPatch,
    actor: ManageUsers,
    request: Request,
    session: DbSession,
) -> UserOut:
    target = await users_admin.get_user_or_404(session, user_id)
    users_admin.manage_scope_or_403(actor, target)

    async def record(action: AuditAction) -> None:
        await record_audit(
            request,
            action=action,
            actor_id=actor.id,
            target_type="user",
            target_id=str(target.id),
        )

    role_changed = False
    if body.role is not None and body.role.value != target.role:
        if actor.role != UserRole.OWNER.value:
            raise forbidden("Only the owner changes roles")
        await users_admin.last_owner_guard(session, target)
        target.role = body.role.value
        role_changed = True
        await record(AuditAction.USER_ROLE_CHANGE)

    if body.status is not None and body.status.value != target.status:
        if actor.id == target.id:
            raise forbidden("You cannot deactivate yourself")
        if body.status is UserStatus.DEACTIVATED:
            await users_admin.last_owner_guard(session, target)
            await users_admin.deactivate_cascade(session, target)
        target.status = body.status.value
        await record(AuditAction.USER_STATUS_CHANGE)

    if body.email is not None:
        await users_admin.change_email(session, target, body.email)
        await record(AuditAction.USER_EMAIL_CHANGE)

    if body.full_name is not None and body.full_name != target.full_name:
        target.full_name = body.full_name
        await record(AuditAction.USER_PROFILE_UPDATE)

    await session.commit()

    if role_changed:
        # Two rails by design: the org-security broadcast + the personal note.
        await dispatch_from_request(
            request,
            session,
            event="security.role_changed",
            source_ref=f"user/{target.id}",
            params={
                "user_name": target.full_name,
                "new_role": target.role,
                "actor_name": actor.full_name,
            },
        )
        await dispatch_from_request(
            request,
            session,
            event="account.role_changed",
            target_user_id=target.id,
            params={"new_role": target.role},
        )
    return UserOut.model_validate(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int, actor: ManageUsers, request: Request, session: DbSession
) -> None:
    """Hard delete (owner-only): auth data cascades, audit entries survive."""
    if actor.role != UserRole.OWNER.value:
        raise forbidden("Only the owner deletes accounts")
    target = await users_admin.get_user_or_404(session, user_id)
    if actor.id == target.id:
        raise forbidden("You cannot delete yourself")
    await users_admin.last_owner_guard(session, target)

    await session.delete(target)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.USER_DELETE,
        actor_id=actor.id,
        target_type="user",
        target_id=str(user_id),
    )


@router.post("/users/{user_id}/reset-password", dependencies=[SensitiveResponse])
async def reset_user_password(
    user_id: int, actor: ManageUsers, request: Request, session: DbSession
) -> AdminResetOut:
    """Primary path with SMTP: a 1h reset letter; without it — a temp password.

    A deactivated target always gets the temp password: the reset worker mails
    ACTIVE users only (anti-enumeration), so a queued letter would vanish
    silently while the admin sees success.
    """
    target = await users_admin.get_user_or_404(session, user_id)
    users_admin.guard_admin_reset(actor, target)
    is_active = target.status == UserStatus.ACTIVE.value
    if is_active and await email_service.smtp_available(session):
        await queue_password_reset(request, email=target.email)
        out = AdminResetOut(mode="link")
    else:
        temp_password = await users_admin.admin_reset_password(session, target)
        await session.commit()
        out = AdminResetOut(mode="temp_password", temp_password=temp_password)
        await dispatch_from_request(
            request, session, event="account.temp_password", target_user_id=target.id
        )
    await record_audit(
        request,
        action=AuditAction.PASSWORD_RESET,
        actor_id=actor.id,
        target_type="user",
        target_id=str(target.id),
        meta={"mode": out.mode},
    )
    return out


@router.post("/users/{user_id}/terminate-sessions", status_code=status.HTTP_204_NO_CONTENT)
async def terminate_sessions(
    user_id: int, actor: ManageUsers, request: Request, session: DbSession
) -> None:

    target = await users_admin.get_user_or_404(session, user_id)
    users_admin.manage_scope_or_403(actor, target)
    count = await sessions_service.end_all_sessions(session, user_id=target.id)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.USER_SESSIONS_TERMINATE,
        actor_id=actor.id,
        target_type="user",
        target_id=str(target.id),
        meta={"sessions": count},
    )


@router.get("/audit-log")
async def read_audit_log(
    actor: ReadAudit,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    actor_id: Annotated[list[int] | None, Query()] = None,
    action_group: Annotated[list[str] | None, Query()] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> AuditLogPage:
    """Owner-only journal view, newest first; search hits object, actor and IP."""
    del actor
    stmt = sa.select(AuditLog).order_by(AuditLog.id.desc())
    if q:
        needle = q.strip()
        stmt = stmt.where(
            sa.or_(
                AuditLog.target_id.icontains(needle, autoescape=True),
                AuditLog.ip.icontains(needle, autoescape=True),
                AuditLog.actor_email.icontains(needle, autoescape=True),
            )
        )
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id.in_(actor_id))
    if action_group:
        prefixes: list[str] = []
        for group in action_group:
            if group not in AUDIT_ACTION_GROUPS:
                raise field_validation_error(
                    "action_group", " | ".join(sorted(AUDIT_ACTION_GROUPS))
                )
            prefixes.extend(AUDIT_ACTION_GROUPS[group])
        stmt = stmt.where(sa.or_(*[AuditLog.action.like(f"{p}%") for p in prefixes]))
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AuditLog.created_at <= date_to)

    total, page = await offset_window(session, stmt, params)
    rows = await session.execute(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return AuditLogPage(
        items=[AuditLogOut.model_validate(entry) for entry in rows.scalars()],
        total=total,
        page=page,
        per_page=params.per_page,
        groups=list(AUDIT_ACTION_GROUPS),
    )
