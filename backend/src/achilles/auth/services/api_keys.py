"""API keys: machine access, key → user_id → role/ACL — authentication.html#api-keys."""

from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import API_KEY_EXPIRY_CHOICES, UserStatus
from achilles.auth.models import ApiKey, User
from achilles.auth.security.tokens import generate_api_key, hash_token, is_expired
from achilles.auth.services.sessions import token_expired, token_invalid

# last_used_at is a coarse display field; minute precision drops ~59/60 of the writes.
LAST_USED_DEBOUNCE = timedelta(minutes=1)


_SCOPE_ACCESS = "access"
_SCOPE_SOURCES = "sources"


def build_scope(sources: list[int] | None) -> dict[str, Any]:
    # Two axes (never wider than the owner): access is read-only in v1,
    # sources narrows from "all available" (null) to an explicit list.
    return {_SCOPE_ACCESS: "read", _SCOPE_SOURCES: sources}


def parse_scope(scope: dict[str, object]) -> list[int] | None:
    """The reader half of build_scope: the source narrowing, or None for unscoped.

    Writer and reader of the scope shape live in one module, so a change to
    build_scope surfaces here rather than silently widening a consumer that
    open-codes ``scope.get("sources")``.
    """
    sources = scope.get(_SCOPE_SOURCES)
    if not isinstance(sources, list):
        return None
    return [int(source_id) for source_id in sources]


async def create_key(
    session: AsyncSession,
    *,
    owner: User,
    name: str | None,
    expires_in_days: int | None,
    sources: list[int] | None,
    now: datetime,
) -> tuple[str, ApiKey]:
    """Returns (raw key — shown exactly once, row)."""
    if expires_in_days is not None and expires_in_days not in API_KEY_EXPIRY_CHOICES:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            "Unsupported key lifetime",
            errors=[{"field": "expires_in_days", "message": "allowed: 30, 90, 365 or null"}],
        )
    raw, key_hash, prefix = generate_api_key()
    row = ApiKey(
        user_id=owner.id,
        key_hash=key_hash,
        prefix=prefix,
        name=name,
        scope=build_scope(sources),
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    session.add(row)
    await session.flush()
    return raw, row


async def rename_key(session: AsyncSession, *, row: ApiKey, name: str | None) -> ApiKey:
    """Set the owner-facing label; None clears it back to the prefix display."""
    row.name = name
    await session.flush()
    return row


async def resolve_key_identity(
    session: AsyncSession, raw_key: str, *, now: datetime
) -> tuple[ApiKey, User]:
    """Key → user; refusals stay generic 401s."""
    row = await session.scalar(sa.select(ApiKey).where(ApiKey.key_hash == hash_token(raw_key)))
    if row is None or row.is_revoked:
        raise token_invalid()
    if row.expires_at is not None and is_expired(row.expires_at, now):
        raise token_expired()
    user = await session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise token_invalid()
    return row, user


async def touch_last_used(session: AsyncSession, key_id: int, *, now: datetime) -> bool:
    """Debounced write; returns whether the row was actually touched."""
    result = await session.execute(
        sa.update(ApiKey)
        .where(
            ApiKey.id == key_id,
            sa.or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < now - LAST_USED_DEBOUNCE),
        )
        .values(last_used_at=now)
    )
    return bool(cast("sa.CursorResult[Any]", result).rowcount)


async def list_keys(session: AsyncSession, *, user_id: int) -> list[ApiKey]:
    """Newest first, matching the company-wide oversight list."""
    return list(
        await session.scalars(
            sa.select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.id.desc())
        )
    )
