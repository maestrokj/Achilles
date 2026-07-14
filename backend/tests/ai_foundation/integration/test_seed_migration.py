"""Migration seed: Platform provider, builtin catalog, presets, singleton (tests.html, P1).

The downgrade/upgrade round-trip runs on its own container — replaying DDL on
the shared session-scoped database would yank tables from under other tests.
"""

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from achilles.ai_foundation.models import AiModel, AiProvider, PromptSettings, Tool
from tests.conftest import BACKEND_DIR

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_platform_provider_seeded(db_session: AsyncSession) -> None:
    platform = (
        await db_session.execute(sa.select(AiProvider).where(AiProvider.is_system))
    ).scalar_one()
    assert platform.name == "Platform"
    assert platform.kind == "platform"
    assert platform.adapter == "openai_compatible"
    assert platform.base_url  # the CHECK guarantees it; pin the seed anyway
    assert platform.api_key_enc is None


async def test_builtin_models_carry_intrinsics(db_session: AsyncSession) -> None:
    models = (
        (await db_session.execute(sa.select(AiModel).where(AiModel.origin == "builtin")))
        .scalars()
        .all()
    )
    by_id = {m.model_id: m for m in models}
    assert set(by_id) == {"BAAI/bge-m3", "Qwen/Qwen3-Embedding-0.6B"}
    for model in models:
        assert model.model_type == "embedding"
        assert model.is_enabled  # enabled ≠ loaded: weights arrive on assignment
        assert model.meta is not None
        assert model.meta["embedding_dim"] == 1024
        assert model.meta["max_input_tokens"] > 0


async def test_tool_presets_seeded_disabled(db_session: AsyncSession) -> None:
    tools = (await db_session.execute(sa.select(Tool))).scalars().all()
    assert {t.name for t in tools} == {"web_search", "fetch_url"}
    for tool in tools:
        assert tool.source == "preset"
        assert tool.access == "read_only"
        assert not tool.chat_enabled
        assert not tool.agents_allowed


async def test_prompt_singleton_seeded_null(db_session: AsyncSession) -> None:
    row = (await db_session.execute(sa.select(PromptSettings))).scalar_one()
    assert row.id == 1
    assert row.safety_text is None
    assert row.org_text is None


async def test_raw_delete_of_system_provider_bounces(db_session: AsyncSession) -> None:
    # Past the service layer on purpose: the lock is the DB trigger.
    with pytest.raises(DBAPIError, match="system provider is protected"):
        await db_session.execute(sa.delete(AiProvider).where(AiProvider.is_system))
        await db_session.commit()
    await db_session.rollback()

    still_there = (
        await db_session.execute(sa.select(AiProvider).where(AiProvider.is_system))
    ).scalar_one()
    assert still_there.name == "Platform"


async def _seeded_provider_names(url: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT name FROM ai_providers"))
            return list(result.scalars().all())
    finally:
        await engine.dispose()


def test_downgrade_upgrade_roundtrip() -> None:
    # Sync on purpose: alembic's env.py calls asyncio.run and cannot execute
    # inside an already-running event loop.
    with PostgresContainer("pgvector/pgvector:pg17", driver="asyncpg") as pg:
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        cfg.set_main_option("sqlalchemy.url", pg.get_connection_url())
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "20260702_140000")
        command.upgrade(cfg, "head")

        assert asyncio.run(_seeded_provider_names(pg.get_connection_url())) == ["Platform"]
