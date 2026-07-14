"""Admin test scaffold: auth fixtures + the platform_settings singleton reset.

The settings tests PATCH the singleton, so isolation resets it by UPDATE
(the row is migration-seeded, CHECK id=1 — TRUNCATE would break every reader).
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.auth.constants import UserRole
from tests.auth.integration.conftest import (
    AUTH_TABLES,
    AuthorizeFn,
    authorize,
    db_engine,
    db_session,
    hibp_clean,
    login,
    redis_durable,
)
from tests.conftest import FlushRedis
from tests.factories.users import create_user
from tests.knowledge_store.conftest import RESET_PLATFORM_SETTINGS

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(AUTH_TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(sa.text(RESET_PLATFORM_SETTINGS))
    await flush_redis()


@pytest.fixture
async def as_owner(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    owner = await create_user(db_session, role=UserRole.OWNER.value)
    await authorize(owner.email)


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


@pytest.fixture
async def as_member(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)
