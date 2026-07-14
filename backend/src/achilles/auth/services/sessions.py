"""Session lifecycle: refresh-token families, rotation with grace, reuse detection.

Design: authentication.html#tokens-sessions / #refresh-rotation. Every refresh
rotates the token; reuse outside the ~10s grace kills the whole family.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.constants import (
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    REFRESH_ROTATION_GRACE,
    UserStatus,
)
from achilles.auth.models import RefreshToken, User
from achilles.auth.security.tokens import generate_token, hash_token, is_expired
from achilles.infra.redis import PREFIX_GRACE
from achilles.knowledge_store.services import platform

# Grace cache on redis-durable: old token hash → the pair it was rotated into.
_GRACE_KEY = PREFIX_GRACE + "refresh:{token_hash}"


@dataclass(frozen=True, slots=True)
class SessionTtls:
    """The org TTLs (platform_settings, seconds in the DB) as timedeltas."""

    access: timedelta
    sliding: timedelta
    absolute: timedelta


async def effective_ttls(session: AsyncSession) -> SessionTtls:
    """Session TTLs come from the Owner-edited singleton, not constants."""
    row = await platform.get_platform_settings(session)
    return SessionTtls(
        access=timedelta(seconds=row.access_token_ttl),
        sliding=timedelta(seconds=row.refresh_token_ttl),
        absolute=timedelta(seconds=row.session_absolute_ttl),
    )


def token_invalid() -> ApiError:
    return ApiError(401, CODE_TOKEN_INVALID, "Unauthorized", "Invalid or unknown token")


class ReuseDetectedError(ApiError):
    """Replay of a revoked refresh token — the family-kill branch of `rotate`.

    Same wire response as `token_invalid()` (an attacker learns nothing), but the
    route can tell it apart and leave an audit trace of who was targeted.
    """

    def __init__(self, *, user_id: int, family_id: uuid.UUID) -> None:
        super().__init__(401, CODE_TOKEN_INVALID, "Unauthorized", "Invalid or unknown token")
        self.user_id = user_id
        self.family_id = family_id


def token_expired() -> ApiError:
    return ApiError(401, CODE_TOKEN_EXPIRED, "Unauthorized", "Token has expired")


@dataclass(frozen=True, slots=True)
class RefreshResult:
    user: User
    raw_refresh_token: str
    remember_me: bool


async def start_session(
    session: AsyncSession,
    *,
    user: User,
    now: datetime,
    ttls: SessionTtls,
    remember_me: bool,
    user_agent: str | None,
    ip: str | None,
) -> str:
    """Open a new token family; returns the raw refresh token (cookie material)."""
    raw = generate_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            family_id=uuid.uuid7(),
            expires_at=now + ttls.sliding,
            absolute_expires_at=now + ttls.absolute,
            remember_me=remember_me,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await session.flush()
    return raw


async def rotate(
    session: AsyncSession,
    redis: Redis,
    *,
    raw_token: str,
    now: datetime,
    ttls: SessionTtls,
    user_agent: str | None,
    ip: str | None,
) -> RefreshResult:
    """Exchange a refresh token for a new one (same family), with tab-race grace."""
    token_hash = hash_token(raw_token)
    # FOR UPDATE serialises concurrent refreshes of the same cookie (two tabs,
    # StrictMode double-fetch). Without it both readers see the parent live, each
    # revokes it and inserts its own child — several live tokens pile up in one
    # family. Locked, the loser blocks, then re-reads is_revoked=True and takes
    # the grace path below (the exact tab-race the grace cache exists for).
    row = await session.scalar(
        sa.select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    if row is None:
        raise token_invalid()

    user = await session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise token_invalid()

    if row.is_revoked:
        grace_raw = await redis.get(_GRACE_KEY.format(token_hash=token_hash))
        if grace_raw is not None:
            cached: dict[str, str] = json.loads(grace_raw)
            return RefreshResult(
                user=user,
                raw_refresh_token=cached["refresh_token"],
                remember_me=row.remember_me,
            )
        # Reuse beyond the grace window — someone replayed an old token: kill the family.
        await session.execute(
            sa.update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .values(is_revoked=True)
        )
        raise ReuseDetectedError(user_id=row.user_id, family_id=row.family_id)

    if is_expired(row.expires_at, now) or is_expired(row.absolute_expires_at, now):
        raise token_expired()

    new_raw = generate_token()
    row.is_revoked = True
    session.add(
        RefreshToken(
            user_id=row.user_id,
            token_hash=hash_token(new_raw),
            family_id=row.family_id,
            # Sliding, but never past the family's absolute ceiling.
            expires_at=min(now + ttls.sliding, row.absolute_expires_at),
            absolute_expires_at=row.absolute_expires_at,
            remember_me=row.remember_me,
            user_agent=user_agent,
            ip=ip,
        )
    )
    await session.flush()
    await redis.set(
        _GRACE_KEY.format(token_hash=token_hash),
        json.dumps({"refresh_token": new_raw}),
        ex=REFRESH_ROTATION_GRACE,
    )
    return RefreshResult(user=user, raw_refresh_token=new_raw, remember_me=row.remember_me)


async def end_session(session: AsyncSession, *, raw_token: str) -> RefreshToken | None:
    """Delete the session identified by the refresh cookie; None if unknown."""
    row = await session.scalar(
        sa.select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if row is None or row.is_revoked:
        return None
    await session.delete(row)
    await session.flush()
    return row


async def end_all_sessions(session: AsyncSession, *, user_id: int) -> int:
    result = await session.execute(sa.delete(RefreshToken).where(RefreshToken.user_id == user_id))
    return cast("sa.CursorResult[Any]", result).rowcount or 0


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """One live device session — the current, non-revoked row of a token family."""

    family_id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    is_current: bool


async def current_family(session: AsyncSession, *, raw_token: str | None) -> uuid.UUID | None:
    """The family behind the caller's refresh cookie, if it is still live."""
    if not raw_token:
        return None
    return await session.scalar(
        sa.select(RefreshToken.family_id).where(
            RefreshToken.token_hash == hash_token(raw_token), RefreshToken.is_revoked.is_(False)
        )
    )


async def list_active_sessions(
    session: AsyncSession, *, user_id: int, now: datetime, current: uuid.UUID | None
) -> list[ActiveSession]:
    """Live sessions of the user — one entry per family, newest sign-in first.

    ``created_at`` is the family's *first* row (the sign-in), not the current one:
    every refresh inserts a fresh row under the same family_id, so the live row's
    own timestamp would read as "just now" and mask a weeks-old session.
    """
    # First-row time per family, over *all* rows incl. the revoked rotations —
    # so a separate grouped subquery, not a window (WHERE would hide them).
    started = (
        sa.select(
            RefreshToken.family_id.label("family_id"),
            sa.func.min(RefreshToken.created_at).label("started_at"),
        )
        .where(RefreshToken.user_id == user_id)
        .group_by(RefreshToken.family_id)
        .subquery()
    )
    # One row per family via DISTINCT ON: the invariant is a single live token per
    # family, but should a rotation race ever leak a second, the device list must
    # still count the family once rather than showing the same session twice.
    live = (
        sa.select(
            RefreshToken.family_id.label("family_id"),
            RefreshToken.user_agent.label("user_agent"),
            RefreshToken.ip.label("ip"),
        )
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked.is_(False),
            RefreshToken.expires_at > now,
            RefreshToken.absolute_expires_at > now,
        )
        .distinct(RefreshToken.family_id)
        .order_by(RefreshToken.family_id, RefreshToken.created_at.desc())
        .subquery()
    )
    rows = await session.execute(
        sa.select(live.c.family_id, live.c.user_agent, live.c.ip, started.c.started_at)
        .join(started, started.c.family_id == live.c.family_id)
        .order_by(started.c.started_at.desc())
    )
    return [
        ActiveSession(
            family_id=row.family_id,
            user_agent=row.user_agent,
            ip=row.ip,
            created_at=row.started_at,
            is_current=row.family_id == current,
        )
        for row in rows
    ]


async def revoke_family(session: AsyncSession, *, user_id: int, family_id: uuid.UUID) -> int:
    """Delete every row of one family owned by the user; 0 if not theirs / unknown."""
    result = await session.execute(
        sa.delete(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.family_id == family_id
        )
    )
    return cast("sa.CursorResult[Any]", result).rowcount or 0


async def revoke_other_families(
    session: AsyncSession, *, user_id: int, keep: uuid.UUID | None
) -> int:
    """Delete all of the user's sessions except the given (current) family."""
    stmt = sa.delete(RefreshToken).where(RefreshToken.user_id == user_id)
    if keep is not None:
        stmt = stmt.where(RefreshToken.family_id != keep)
    result = await session.execute(stmt)
    return cast("sa.CursorResult[Any]", result).rowcount or 0
