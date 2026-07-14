"""Password flows: change · forgot · reset.

Design: authentication.html#change-password / #forgot-password,
protection.html#password-policy.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import CODE_RESET_EXPIRED, RESET_TOKEN_TTL, UserStatus
from achilles.auth.models import RefreshToken, ResetToken, User
from achilles.auth.security.passwords import hash_password_async
from achilles.auth.security.tokens import generate_token, hash_token, is_expired
from achilles.auth.services.bootstrap import password_policy_or_422


def same_as_current_422() -> ApiError:
    return ApiError(
        422,
        CODE_VALIDATION_ERROR,
        "Validation error",
        "New password must differ from the current one",
        errors=[{"field": "new_password", "message": "must differ from the current password"}],
    )


def reset_expired() -> ApiError:
    # Used and expired are indistinguishable to the client — one answer.
    return ApiError(410, CODE_RESET_EXPIRED, "Gone", "Reset link has expired or was used")


async def apply_new_password(
    session: AsyncSession,
    user: User,
    new_password: str,
    *,
    keep_token_hash: str | None = None,
) -> None:
    """Validate, hash, store; revoke refresh tokens (optionally sparing one session)."""
    await password_policy_or_422(new_password)
    user.password_hash = await hash_password_async(new_password)
    user.must_change_password = False
    revoke = sa.delete(RefreshToken).where(RefreshToken.user_id == user.id)
    if keep_token_hash is not None:
        revoke = revoke.where(RefreshToken.token_hash != keep_token_hash)
    await session.execute(revoke)
    await session.flush()


async def issue_reset_token(session: AsyncSession, user: User, *, now: datetime) -> str:
    """One-time reset link material; a new link kills the previous one."""
    await session.execute(sa.delete(ResetToken).where(ResetToken.user_id == user.id))
    raw = generate_token()
    session.add(
        ResetToken(user_id=user.id, token_hash=hash_token(raw), expires_at=now + RESET_TOKEN_TTL)
    )
    await session.flush()
    return raw


async def consume_reset_token(session: AsyncSession, raw_token: str, *, now: datetime) -> User:
    row = await session.scalar(
        sa.select(ResetToken).where(ResetToken.token_hash == hash_token(raw_token))
    )
    if row is None or row.used_at is not None or is_expired(row.expires_at, now):
        raise reset_expired()
    user = await session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise reset_expired()
    row.used_at = now
    return user
