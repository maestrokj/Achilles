"""Typed user factory helpers.

The argon2 hash for the shared default password is computed once per run —
real-parameter hashing (19 MiB) across hundreds of tests would dominate runtime.
"""

import functools
import itertools

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import AuthProvider, UserRole, UserStatus
from achilles.auth.models import User
from achilles.auth.security.passwords import hash_password

DEFAULT_PASSWORD = "correct-horse-battery-staple-2026"


@functools.cache
def default_password_hash() -> str:
    # Lazy: importing the module (e.g. for DEFAULT_PASSWORD alone) stays free.
    return hash_password(DEFAULT_PASSWORD)


_seq = itertools.count(1)


def build_user(
    *,
    email: str | None = None,
    full_name: str | None = None,
    role: str = UserRole.MEMBER.value,
    status: str = UserStatus.ACTIVE.value,
    password: str | None = None,
    must_change_password: bool = False,
) -> User:
    n = next(_seq)
    password_hash = default_password_hash() if password is None else hash_password(password)
    return User(
        email=email or f"user{n}@example.com",
        full_name=full_name or f"User {n}",
        role=role,
        status=status,
        auth_provider=AuthProvider.LOCAL.value,
        password_hash=password_hash,
        must_change_password=must_change_password,
    )


async def create_user(session: AsyncSession, **kwargs: object) -> User:
    user = build_user(**kwargs)  # type: ignore[arg-type]
    session.add(user)
    await session.commit()
    return user
