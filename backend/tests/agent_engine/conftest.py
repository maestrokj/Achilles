"""Agent Engine test scaffold: KS scaffold + agent tables + AI catalog reset.

Composes the KS table list (which already folds in the auth set) under the
agent tables, restores the migration-seeded AI state (like the QE conftest)
and resets platform_settings — the limits tests PATCH its agent knobs.
The scripted ChatClient lives in tests/factories/llm.py, shared with the
harness suite.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth.integration.conftest import (
    authorize,
    db_engine,
    db_session,
    hibp_clean,
    login,
    redis_durable,
)
from tests.conftest import FlushRedis
from tests.factories.ai import reset_ai_catalog
from tests.knowledge_store.conftest import KS_TABLES, RESET_PLATFORM_SETTINGS

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]

_TABLES = (
    "agent_runs",
    "agent_tools",
    "agents",
    "chat_models",
    "agent_models",
    *KS_TABLES,
)


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
        await conn.execute(sa.text(RESET_PLATFORM_SETTINGS))
    await flush_redis()
