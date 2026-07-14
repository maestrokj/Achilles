"""MCP surface scaffold: KS tables + the platform_settings singleton reset
(the kill-switch test flips mcp_enabled and must not leak it)."""

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
from tests.knowledge_store.conftest import KS_TABLES, RESET_PLATFORM_SETTINGS

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(KS_TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(sa.text(RESET_PLATFORM_SETTINGS))
    await flush_redis()
