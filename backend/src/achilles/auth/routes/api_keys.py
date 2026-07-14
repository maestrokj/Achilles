"""API-key routes: self-service CRUD + admin issuance/oversight."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.pagination import OffsetPage, OffsetParams, Page, offset_window
from achilles.api.problems import CODE_NOT_FOUND, ApiError
from achilles.api.security_headers import SensitiveResponse
from achilles.api.serialization import UtcDateTime
from achilles.auth.constants import API_KEY_NAME_MAX_LEN, Permission, has_permission
from achilles.auth.dependencies import CurrentUser, require
from achilles.auth.models import ApiKey, User
from achilles.auth.routes.common import record_audit
from achilles.auth.services import api_keys, users_admin
from achilles.auth.services.audit import AuditAction
from achilles.auth.services.users_admin import forbidden, user_search_clause
from achilles.db.dependencies import DbSession

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
admin_router = APIRouter(prefix="/admin/api-keys", tags=["admin"])

ManageKeys = Annotated[User, require(Permission.API_KEYS_MANAGE)]

type KeyStatus = Literal["active", "expired", "revoked"]


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    prefix: str
    name: str | None
    scope: dict[str, Any]
    expires_at: UtcDateTime | None
    last_used_at: UtcDateTime | None
    is_revoked: bool
    revoked_at: UtcDateTime | None
    created_at: UtcDateTime


class ApiKeyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=API_KEY_NAME_MAX_LEN)
    expires_in_days: int | None = None
    sources: list[int] | None = None
    user_id: int | None = None  # admin issuance for another account


class ApiKeyUpdate(BaseModel):
    """Rename only — scope and lifetime are fixed at issue."""

    name: str | None = Field(default=None, max_length=API_KEY_NAME_MAX_LEN)


class ApiKeyCreated(ApiKeyOut):
    key: str
    """The raw key — shown exactly once, only the hash is stored."""


def _clean_name(raw: str | None) -> str | None:
    """Trim; blank collapses to None so the list falls back to the prefix."""
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


class KeyOwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str


class AdminApiKeyOut(ApiKeyOut):
    owner: KeyOwnerOut
    status: KeyStatus


def _key_status(key: ApiKey, now: datetime) -> KeyStatus:
    """Same predicates as the by_status filter dict below — keep them in step."""
    if key.is_revoked:
        return "revoked"
    if key.expires_at is not None and key.expires_at <= now:
        return "expired"
    return "active"


def _can_manage_others(role: str) -> bool:
    return has_permission(role, Permission.API_KEYS_MANAGE)


async def _load_manageable_key(session: AsyncSession, user: User, key_id: int) -> ApiKey:
    """A key the caller may act on: own, or (Owner: anyone · Admin: members only).

    Ownership guard (IDOR): a permission alone is not enough, and the manage
    scope still binds — Admin cannot touch an Owner's/Admin's key.
    """
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such key")
    if row.user_id != user.id:
        if not _can_manage_others(user.role):
            raise forbidden("Not the owner of this key")
        users_admin.manage_scope_or_403(
            user, await users_admin.get_user_or_404(session, row.user_id)
        )
    return row


@admin_router.get("")
async def list_company_keys(
    actor: ManageKeys,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    key_status: Annotated[list[KeyStatus] | None, Query(alias="status")] = None,
) -> OffsetPage[AdminApiKeyOut]:
    """Oversight list: every key in the company with its owner, newest first."""
    del actor
    now = datetime.now(UTC)
    stmt = sa.select(ApiKey, User).join(User, User.id == ApiKey.user_id).order_by(ApiKey.id.desc())
    if q:
        needle = q.strip()
        stmt = stmt.where(
            sa.or_(
                ApiKey.name.icontains(needle, autoescape=True),
                ApiKey.prefix.icontains(needle, autoescape=True),
                user_search_clause(needle),
            )
        )
    if key_status:
        by_status = {
            "revoked": ApiKey.is_revoked.is_(True),
            "expired": sa.and_(
                sa.not_(ApiKey.is_revoked),
                ApiKey.expires_at.is_not(None),
                ApiKey.expires_at <= now,
            ),
            "active": sa.and_(
                sa.not_(ApiKey.is_revoked),
                sa.or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            ),
        }
        stmt = stmt.where(sa.or_(*[by_status[s] for s in key_status]))

    total, page = await offset_window(session, stmt, params)
    rows = await session.execute(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return OffsetPage(
        items=[
            AdminApiKeyOut(
                **ApiKeyOut.model_validate(key).model_dump(),
                owner=KeyOwnerOut.model_validate(owner),
                status=_key_status(key, now),
            )
            for key, owner in rows.tuples()
        ],
        total=total,
        page=page,
        per_page=params.per_page,
    )


@router.get("")
async def list_api_keys(
    user: CurrentUser, session: DbSession, user_id: int | None = None
) -> Page[ApiKeyOut]:
    """Own keys; Owner inspects anyone, Admin only members (?user_id)."""
    target_id = user.id
    if user_id is not None and user_id != user.id:
        if not _can_manage_others(user.role):
            raise forbidden("Not allowed to inspect other users' keys")
        # Scope, not just permission: Admin manages members only (authorization.html).
        users_admin.manage_scope_or_403(user, await users_admin.get_user_or_404(session, user_id))
        target_id = user_id
    rows = await api_keys.list_keys(session, user_id=target_id)
    return Page(items=[ApiKeyOut.model_validate(r) for r in rows])


# The response carries the raw key (shown exactly once) — never cached.
@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[SensitiveResponse])
async def create_api_key(
    body: ApiKeyCreate, user: CurrentUser, request: Request, session: DbSession
) -> ApiKeyCreated:
    owner = user
    if body.user_id is not None and body.user_id != user.id:
        if not _can_manage_others(user.role):
            raise forbidden("Not allowed to issue keys for other users")
        owner = await users_admin.get_user_or_404(session, body.user_id)
        users_admin.manage_scope_or_403(user, owner)

    raw, row = await api_keys.create_key(
        session,
        owner=owner,
        name=_clean_name(body.name),
        expires_in_days=body.expires_in_days,
        sources=body.sources,
        now=datetime.now(UTC),
    )
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.API_KEY_CREATE,
        actor_id=user.id,
        target_type="api_key",
        target_id=str(row.id),
        meta={"owner_id": owner.id},
    )
    return ApiKeyCreated(key=raw, **ApiKeyOut.model_validate(row).model_dump())


@router.patch("/{key_id}")
async def rename_api_key(
    key_id: int, body: ApiKeyUpdate, user: CurrentUser, request: Request, session: DbSession
) -> ApiKeyOut:
    """Rename a key: own, or (Owner: anyone · Admin: members only) oversight."""
    row = await _load_manageable_key(session, user, key_id)
    await api_keys.rename_key(session, row=row, name=_clean_name(body.name))
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.API_KEY_RENAME,
        actor_id=user.id,
        target_type="api_key",
        target_id=str(key_id),
    )
    return ApiKeyOut.model_validate(row)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: int, user: CurrentUser, request: Request, session: DbSession
) -> None:
    """Instant revoke: own key, or (Owner: anyone · Admin: members only) oversight."""
    row = await _load_manageable_key(session, user, key_id)
    if not row.is_revoked:
        row.is_revoked = True
        row.revoked_at = datetime.now(UTC)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.API_KEY_REVOKE,
        actor_id=user.id,
        target_type="api_key",
        target_id=str(key_id),
    )
