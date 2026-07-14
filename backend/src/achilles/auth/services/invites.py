"""Invitations: the v1 admission floor (invite-only, closed by default).

Design: authentication.html#invite-flow / #admission-model. The invite link is
delivered only by email — delivery doubles as email verification; without SMTP
an invite cannot be created (409 SMTP_NOT_CONFIGURED).
"""

import csv
import io
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_CONFLICT, CODE_NOT_FOUND, CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import (
    CODE_EMAIL_TAKEN,
    CODE_INVITE_EXPIRED,
    CODE_INVITE_USED,
    CODE_SMTP_NOT_CONFIGURED,
    INVITE_TOKEN_TTL,
    AuthProvider,
    UserRole,
    UserStatus,
)
from achilles.auth.models import InviteToken, User
from achilles.auth.security.passwords import hash_password_async
from achilles.auth.security.tokens import generate_token, hash_token, is_expired
from achilles.auth.services import identity_bridge
from achilles.auth.services.bootstrap import password_policy_or_422
from achilles.auth.services.users_admin import email_taken, forbidden

BULK_INVITE_MAX_ROWS = 500


def smtp_not_configured() -> ApiError:
    return ApiError(
        409,
        CODE_SMTP_NOT_CONFIGURED,
        "Conflict",
        "SMTP is not configured — invites cannot be delivered",
    )


def invite_scope_or_403(actor: User, role: str) -> None:
    """Owner assigns any role; Admin invites members only."""
    if actor.role == UserRole.OWNER.value:
        return
    if role != UserRole.MEMBER.value:
        raise forbidden("Admins invite members only")


async def create_invite(
    session: AsyncSession, *, actor: User, email: str, role: str, now: datetime
) -> tuple[str, InviteToken]:
    """Returns (raw token — goes into the email link, row). A re-invite kills the old link."""
    email = email.lower()
    invite_scope_or_403(actor, role)
    if await email_taken(session, email):
        raise ApiError(409, CODE_EMAIL_TAKEN, "Conflict", "An account with this email exists")

    await session.execute(
        sa.delete(InviteToken).where(
            sa.func.lower(InviteToken.email) == email, InviteToken.accepted_at.is_(None)
        )
    )
    raw = generate_token()
    row = InviteToken(
        email=email,
        role=role,
        token_hash=hash_token(raw),
        invited_by=actor.id,
        expires_at=now + INVITE_TOKEN_TTL,
    )
    session.add(row)
    await session.flush()
    return raw, row


async def get_invite_or_404(session: AsyncSession, invite_id: int) -> InviteToken:
    row = await session.get(InviteToken, invite_id)
    if row is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such invite")
    return row


async def resend_invite(
    session: AsyncSession, *, actor: User, invite_id: int, now: datetime
) -> tuple[str, InviteToken]:
    """Rotate the link on the same row: the old token dies, the clock restarts."""
    row = await get_invite_or_404(session, invite_id)
    invite_scope_or_403(actor, row.role)
    if row.accepted_at is not None:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "The invite is already accepted")
    raw = generate_token()
    row.token_hash = hash_token(raw)
    row.expires_at = now + INVITE_TOKEN_TTL
    await session.flush()
    return raw, row


async def revoke_invite(session: AsyncSession, *, actor: User, invite_id: int) -> InviteToken:
    """Kill the pending link; an accepted invite is history, not a revocable object."""
    row = await get_invite_or_404(session, invite_id)
    invite_scope_or_403(actor, row.role)
    if row.accepted_at is not None:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "The invite is already accepted")
    await session.delete(row)
    await session.flush()
    return row


async def accept_invite(
    session: AsyncSession, raw_token: str, *, full_name: str, password: str, now: datetime
) -> User:
    row = await session.scalar(
        sa.select(InviteToken).where(InviteToken.token_hash == hash_token(raw_token))
    )
    if row is None or is_expired(row.expires_at, now):
        raise ApiError(410, CODE_INVITE_EXPIRED, "Gone", "Invite link has expired")
    if row.accepted_at is not None:
        raise ApiError(410, CODE_INVITE_USED, "Gone", "Invite link was already used")
    if await email_taken(session, row.email.lower()):
        raise ApiError(409, CODE_EMAIL_TAKEN, "Conflict", "An account with this email exists")

    await password_policy_or_422(password)
    row.accepted_at = now
    user = User(
        email=row.email.lower(),
        password_hash=await hash_password_async(password),
        full_name=full_name,
        role=row.role,
        status=UserStatus.ACTIVE.value,
        auth_provider=AuthProvider.LOCAL.value,
        last_login_at=now,
    )
    session.add(user)
    await session.flush()
    await identity_bridge.auto_link_identity(session, user_id=user.id, email=user.email)
    return user


def parse_bulk_csv(
    payload: bytes, *, default_role: str = UserRole.MEMBER.value
) -> list[tuple[int, str, str, bool]]:
    """CSV rows `email[,role]` → (row_no, email, role, from_default); size-capped with 422.

    A row without its own role column gets `default_role`; the trailing flag is
    True for exactly those rows, so the caller can tell an explicit role from an
    inherited one.
    """
    # A binary masquerading as .csv (a Numbers/Excel document renamed, say)
    # carries NUL bytes; csv.reader raises on those, so reject it up front with
    # a message that names the real problem instead of a 500.
    if b"\x00" in payload:
        raise _not_a_csv()
    reader = csv.reader(io.StringIO(payload.decode("utf-8-sig", errors="replace")))
    rows: list[tuple[int, str, str, bool]] = []
    try:
        for index, record in enumerate(reader, start=1):
            if not record or not "".join(record).strip():
                continue
            email = record[0].strip().lower()
            has_role = len(record) > 1 and bool(record[1].strip())
            role = record[1].strip().lower() if has_role else default_role
            rows.append((index, email, role, not has_role))
            if len(rows) > BULK_INVITE_MAX_ROWS:
                raise ApiError(
                    422,
                    CODE_VALIDATION_ERROR,
                    "Validation error",
                    f"Bulk invite is capped at {BULK_INVITE_MAX_ROWS} rows",
                    errors=[{"field": "file", "message": "too many rows"}],
                )
    except csv.Error as exc:
        raise _not_a_csv() from exc
    return rows


def _not_a_csv() -> ApiError:
    """The upload can't be read as CSV text — a renamed spreadsheet, most often."""
    return ApiError(
        422,
        CODE_VALIDATION_ERROR,
        "Validation error",
        "This file isn't a CSV — export it as CSV (in Numbers or Excel) and try again",
        errors=[{"field": "file", "message": "not a CSV file"}],
    )
