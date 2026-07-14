"""QE test scaffold: KS scaffold + the dialogue tables + AI catalog reset.

Composes the KS table list (which already folds in the auth set and the AI
leaves) under the QE tables, and restores the migration-seeded AI state the
way the ai_foundation conftest does — QE api tests create providers/models
of their own and must not leak them.
"""

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
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
from tests.knowledge_store.conftest import KS_TABLES

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]

QE_TABLES = (
    "retrieval_trace",
    "messages",
    "conversations",
    "access_counter",
    "chat_models",
    "agent_models",
    *KS_TABLES,
)


@pytest.fixture(autouse=True)
def hf_offline(hibp_clean: respx.MockRouter) -> None:
    """The assigned model's tokenizer degrades to chars/4 — no HF egress in tests."""
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(return_value=Response(404))


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(QE_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
    await flush_redis()
