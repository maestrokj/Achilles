"""Messenger link: one-time code binds a chat identity to an account.

Design: authentication.html#entry-channels, data-model.html#link-tokens.
The code travels web → human → bot DM (relay-safe direction: possessing the
code proves possession of the web session). Consumers (bots) arrive in stage 8.
"""

from datetime import datetime

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import (
    BRUTE_IP_WINDOW,
    CODE_ALREADY_LINKED,
    CODE_LINK_EXPIRED,
    LINK_CODE_MAX_ATTEMPTS,
    LINK_CODE_TTL,
    LINK_PLATFORMS,
    UserStatus,
)
from achilles.auth.models import IdentityMapping, LinkToken, User
from achilles.auth.security.tokens import (
    generate_link_code,
    hash_token,
    is_expired,
    normalize_link_code,
)
from achilles.auth.services.brute_force import rate_limited
from achilles.config import Settings
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import PREFIX_BRUTE

_ATTEMPTS_KEY = PREFIX_BRUTE + "link:{platform}:{chat_id}"


def link_page_url(app_settings: Settings, platform: str) -> str:
    """Absolute deep link to the one-time code screen (`/link/:platform`).

    One convention across every messenger surface, so a bot's "not linked" hint
    deep-links straight to the right screen.
    """
    return app_settings.public_url(f"/link/{platform}")


def validate_platform(platform: str) -> None:
    if platform not in LINK_PLATFORMS:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            "Unknown messenger platform",
            errors=[{"field": "platform", "message": "unknown platform"}],
        )


def link_expired() -> ApiError:
    # Expired and used are indistinguishable to the caller — one answer.
    return ApiError(410, CODE_LINK_EXPIRED, "Gone", "Link code has expired or was used")


async def issue_code(session: AsyncSession, *, user: User, now: datetime) -> str:
    """New code invalidates the user's previous pending codes."""
    await session.execute(
        sa.delete(LinkToken).where(LinkToken.user_id == user.id, LinkToken.used_at.is_(None))
    )
    raw = generate_link_code()
    session.add(
        LinkToken(
            user_id=user.id,
            code_hash=hash_token(normalize_link_code(raw)),
            expires_at=now + LINK_CODE_TTL,
        )
    )
    await session.flush()
    return raw


async def guard_chat_attempts(redis: Redis, *, platform: str, chat_id: str, now: datetime) -> None:
    """The bot side brute-forces? 5 wrong codes per chat and the barrier drops."""
    decision = await hit_sliding_window(
        redis,
        _ATTEMPTS_KEY.format(platform=platform, chat_id=chat_id),
        limit=LINK_CODE_MAX_ATTEMPTS,
        window_seconds=int(BRUTE_IP_WINDOW.total_seconds()),
        now=now.timestamp(),
    )
    if not decision.allowed:
        raise rate_limited(decision.retry_after)


async def resolve_identity(
    session: AsyncSession, *, platform: str, platform_user_id: str
) -> tuple[bool, User | None]:
    """(linked, active user) behind a chat identity.

    Linked-but-inactive yields (True, None): the mapping is taken, so the
    caller must not fall through to auto-linking the same chat identity.
    """
    mapping = await session.scalar(
        sa.select(IdentityMapping).where(
            IdentityMapping.source == platform,
            IdentityMapping.source_user_id == platform_user_id,
        )
    )
    if mapping is None:
        return False, None
    user = await session.get(User, mapping.user_id)
    if user is not None and user.status == str(UserStatus.ACTIVE):
        return True, user
    return True, None


async def auto_link_by_email(
    session: AsyncSession, *, platform: str, platform_user_id: str, email: str
) -> User | None:
    """Silent link when the messenger-provisioned email matches an active account.

    Membership alone grants nothing: no matching account — no mapping, and the
    caller falls back to the one-time-code path.
    """
    user = await session.scalar(
        sa.select(User).where(
            sa.func.lower(User.email) == email.lower(),
            User.status == str(UserStatus.ACTIVE),
        )
    )
    if user is None:
        return None
    session.add(
        IdentityMapping(
            user_id=user.id, source=platform, source_user_id=platform_user_id, source_email=email
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:  # UNIQUE(source, source_user_id)
        raise ApiError(
            409, CODE_ALREADY_LINKED, "Conflict", "This chat identity is already linked"
        ) from exc
    return user


async def confirm_code(
    session: AsyncSession,
    *,
    raw_code: str,
    platform: str,
    platform_user_id: str,
    platform_email: str | None,
    now: datetime,
) -> User:
    row = await session.scalar(
        sa.select(LinkToken).where(LinkToken.code_hash == hash_token(normalize_link_code(raw_code)))
    )
    if row is None or row.used_at is not None or is_expired(row.expires_at, now):
        raise link_expired()

    user = await session.get(User, row.user_id)
    if user is None:
        raise link_expired()

    row.used_at = now
    session.add(
        IdentityMapping(
            user_id=user.id,
            source=platform,
            source_user_id=platform_user_id,
            source_email=platform_email,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:  # UNIQUE(source, source_user_id)
        raise ApiError(
            409, CODE_ALREADY_LINKED, "Conflict", "This chat identity is already linked"
        ) from exc
    return user
