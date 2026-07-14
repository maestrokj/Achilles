"""First-owner bootstrap: Setup Wizard and CLI share one advisory-locked path.

Design: authentication.html#setup-wizard. The pg advisory xact-lock kills the
race; the second creator sees the count and gets a conflict.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_CONFLICT, CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import AuthProvider, UserRole, UserStatus
from achilles.auth.models import User
from achilles.auth.security.passwords import hash_password_async, validate_password

# One key guards both entries (wizard + CLI); arbitrary but stable project-wide.
BOOTSTRAP_LOCK_ID = 0xAC1113B0


async def users_exist(session: AsyncSession) -> bool:
    return await session.scalar(sa.select(sa.exists().where(User.id.isnot(None)))) or False


async def password_policy_or_422(password: str) -> None:
    violations = await validate_password(password)
    if violations:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            "Password does not meet the policy",
            errors=[{"field": "password", "message": v} for v in violations],
        )


def email_policy_or_422(email: str) -> None:
    """Reject emails the login schema would refuse.

    Same validator EmailStr runs in the API schemas — the CLI has no schema in
    front of it, and an owner created with an unloginable email bricks the
    one-shot bootstrap.
    """
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            str(exc),
            errors=[{"field": "email", "message": str(exc)}],
        ) from exc


async def create_owner(session: AsyncSession, *, email: str, full_name: str, password: str) -> User:
    """Create the very first account. Raises 409 CONFLICT if someone else won the race."""
    email_policy_or_422(email)
    await password_policy_or_422(password)

    await session.execute(sa.select(sa.func.pg_advisory_xact_lock(BOOTSTRAP_LOCK_ID)))
    if await users_exist(session):
        raise ApiError(409, CODE_CONFLICT, "Conflict", "An owner account already exists")

    owner = User(
        email=email.lower(),
        password_hash=await hash_password_async(password),
        full_name=full_name,
        role=UserRole.OWNER.value,
        status=UserStatus.ACTIVE.value,
        auth_provider=AuthProvider.LOCAL.value,
        last_login_at=datetime.now(UTC),
    )
    session.add(owner)
    await session.flush()
    return owner
