"""User administration: scope rules, last-owner guard, deactivation cascade.

Design: authorization.html (Owner manages admins; Admin manages only members),
users.html / user-card.html contracts.
"""

import secrets
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.models import Agent
from achilles.api.problems import CODE_CONFLICT, CODE_FORBIDDEN, CODE_NOT_FOUND, ApiError
from achilles.auth.constants import CODE_LAST_OWNER_PROTECTED, UserRole, UserStatus
from achilles.auth.models import ApiKey, User
from achilles.auth.security.passwords import hash_password_async
from achilles.auth.services import identity_bridge, sessions

TEMP_PASSWORD_NBYTES = 12


def forbidden(detail: str) -> ApiError:
    return ApiError(403, CODE_FORBIDDEN, "Forbidden", detail)


def user_search_clause(needle: str) -> sa.ColumnElement[bool]:
    """The one name/email search predicate every admin user list shares.

    autoescape: LIKE metacharacters typed into the search box match literally.
    """
    return sa.or_(
        User.full_name.icontains(needle, autoescape=True),
        User.email.icontains(needle, autoescape=True),
    )


def manage_scope_or_403(actor: User, target: User) -> None:
    """Owner manages everyone; Admin manages members only."""
    if actor.role == UserRole.OWNER.value:
        return
    if target.role != UserRole.MEMBER.value:
        raise forbidden("Admins manage members only")


async def get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such user")
    return user


async def last_owner_guard(session: AsyncSession, target: User) -> None:
    """The last active Owner can be neither deleted, deactivated nor downgraded."""
    if target.role != UserRole.OWNER.value or target.status != UserStatus.ACTIVE.value:
        return  # acting on an inactive owner cannot reduce the active-owner count
    owners = await session.scalar(
        sa.select(sa.func.count())
        .select_from(User)
        .where(User.role == UserRole.OWNER.value, User.status == UserStatus.ACTIVE.value)
    )
    if (owners or 0) <= 1:
        raise ApiError(
            403,
            CODE_LAST_OWNER_PROTECTED,
            "Forbidden",
            "The last owner cannot be removed, deactivated or downgraded",
        )


async def deactivate_cascade(session: AsyncSession, target: User) -> None:
    """Deactivation kills sessions, machine access and personal agents immediately.

    Reactivation deliberately does NOT flip agents back — the returning owner
    re-enables them personally (agent-engine/governance.html#lifecycle).
    """
    await sessions.end_all_sessions(session, user_id=target.id)
    await session.execute(
        sa.update(ApiKey)
        # Already-revoked keys keep their original revoked_at.
        .where(ApiKey.user_id == target.id, sa.not_(ApiKey.is_revoked))
        .values(is_revoked=True, revoked_at=datetime.now(UTC))
    )
    await session.execute(
        sa.update(Agent).where(Agent.user_id == target.id).values(enabled=False, next_run_at=None)
    )


async def email_taken(session: AsyncSession, email: str) -> bool:
    return bool(
        await session.scalar(sa.select(sa.exists().where(sa.func.lower(User.email) == email)))
    )


async def change_email(session: AsyncSession, target: User, new_email: str) -> None:
    new_email = new_email.lower()
    if new_email == target.email.lower():
        return
    if await email_taken(session, new_email):
        raise ApiError(409, CODE_CONFLICT, "Conflict", "Email is already in use")
    target.email = new_email
    await sessions.end_all_sessions(session, user_id=target.id)
    await identity_bridge.auto_link_identity(session, user_id=target.id, email=new_email)


async def admin_reset_password(session: AsyncSession, target: User) -> str:
    """SMTP-less fallback: CSPRNG temp password, shown to the admin once."""
    temp_password = secrets.token_urlsafe(TEMP_PASSWORD_NBYTES)
    target.password_hash = await hash_password_async(temp_password)
    target.must_change_password = True
    await sessions.end_all_sessions(session, user_id=target.id)
    return temp_password


def guard_admin_reset(actor: User, target: User) -> None:
    """Shared gate of both reset paths (link and temp password)."""
    manage_scope_or_403(actor, target)
    if actor.id == target.id:
        raise forbidden("Reset your own password via password change")
