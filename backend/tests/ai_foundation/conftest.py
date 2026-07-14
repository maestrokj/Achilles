"""AI Foundation test scaffold: mirrors the KS conftest.

The migration seeds live rows (Platform provider, builtin models, tool
presets, the prompt singleton) — those are reset by UPDATE/DELETE, never
truncated, so every test sees the exact post-migration state.
"""

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
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
from tests.factories.ai import RESET_AI_SEED_SQL, RESTORE_PROMPT_SINGLETON_SQL
from tests.factories.users import create_user

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    """Every AI admin surface sits behind the same gate — one fixture for all suites."""
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


@pytest.fixture(autouse=True)
def embeddings_runtime_mock(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    """The assignment hook warms the runtime best-effort; answer it instead of egress."""
    hibp_clean.post(url__startswith="http://embeddings").mock(
        return_value=Response(200, json={"status": "ok"})
    )
    return hibp_clean


# Leaves that only reference ai_models — safe to truncate with the catalog kept.
# curation_runs rides along: an assignment PATCH journals a MODEL_CHANGE run,
# and a leftover active run would 409 the next test's harvester_embedding swap.
_AI_LEAF_TABLES = (
    "model_assignments",
    "chat_models",
    "agent_models",
    "model_usage",
    "curation_runs",
)


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            sa.text(f"TRUNCATE {', '.join(_AI_LEAF_TABLES)} RESTART IDENTITY CASCADE")
        )
        for statement in RESET_AI_SEED_SQL.strip().split(";"):
            if statement.strip():
                await conn.execute(sa.text(statement))
        await conn.execute(sa.text(f"TRUNCATE {', '.join(AUTH_TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(sa.text(RESTORE_PROMPT_SINGLETON_SQL))
    await flush_redis()
