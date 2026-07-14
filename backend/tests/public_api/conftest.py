"""Public API scaffold: the KS table set (auth folded in); no AI catalog needed —
the external tier degrades without an embedder instead of requiring one."""

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
from tests.knowledge_store.conftest import KS_TABLES

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(KS_TABLES)} RESTART IDENTITY CASCADE"))
    await flush_redis()
