"""AI Foundation factory helpers: providers, catalog models, tools, usage rows."""

import itertools
from collections.abc import Sequence
from datetime import date

import httpx
import respx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from achilles.ai_foundation.constants import EMBEDDING_DIM, AiFunction
from achilles.ai_foundation.models import (
    AiModel,
    AiProvider,
    ChatModel,
    ModelAssignment,
    ModelUsage,
    Tool,
)

_seq = itertools.count(1)

# The compose-internal embeddings runtime — one URL for every respx mock.
EMBEDDINGS_URL = "http://embeddings:80/v1/embeddings"
# Lazy weight-load kick (warm_assigned) — idempotent, mocked where re-warm fires.
EMBEDDINGS_LOAD_URL = "http://embeddings:80/admin/load"
# Per-model runtime state — the re-embed loop and /admin/ai/embedder read it.
EMBEDDINGS_STATUS_URL = "http://embeddings:80/admin/status"
# Memory fit check the assignment PATCH runs before committing.
EMBEDDINGS_PREFLIGHT_URL = "http://embeddings:80/admin/preflight"

# The migration-seeded builtin the suites assign by default.
BUILTIN_EMBEDDING_MODEL = "BAAI/bge-m3"

# The migration seeds live rows (Platform provider, builtin models, tool
# presets, the prompt singleton) — conftests reset them by UPDATE/DELETE,
# never truncate, so every test sees the exact post-migration state.
RESET_AI_SEED_SQL = """
DELETE FROM ai_models WHERE origin <> 'builtin';
DELETE FROM ai_providers WHERE NOT is_system;
UPDATE ai_models SET is_enabled = true, price_input = NULL, price_output = NULL;
DELETE FROM tools WHERE name NOT IN ('web_search', 'fetch_url');
INSERT INTO tools (name, source, access)
    VALUES ('web_search', 'preset', 'read_only'), ('fetch_url', 'preset', 'read_only')
    ON CONFLICT (name) DO NOTHING;
UPDATE tools SET source = 'preset', access = 'read_only', config = NULL,
    credential_enc = NULL, chat_enabled = false, agents_allowed = false,
    status = 'unchecked', last_check_at = NULL;
"""

# TRUNCATE users CASCADE reaches prompt_settings through updated_by — restore
# the singleton instead of updating what may be gone.
RESTORE_PROMPT_SINGLETON_SQL = """
INSERT INTO prompt_settings (id) VALUES (1)
ON CONFLICT (id) DO UPDATE SET safety_text = NULL, org_text = NULL, updated_by = NULL
"""


async def reset_ai_catalog(conn: AsyncConnection) -> None:
    """Restore the migration-seeded catalog + prompt singleton, statement by statement."""
    for statement in f"{RESET_AI_SEED_SQL};{RESTORE_PROMPT_SINGLETON_SQL}".split(";"):
        if statement.strip():
            await conn.execute(sa.text(statement))


def basis(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    """A unit basis vector — orthogonal test embeddings by index."""
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def mock_embed(router: respx.MockRouter, vector: Sequence[float] | None = None) -> respx.Route:
    """Answer the embeddings runtime with one fixed vector (prompt_tokens=7)."""
    return router.post(EMBEDDINGS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": list(vector) if vector is not None else basis(0)}
                ],
                "usage": {"prompt_tokens": 7},
            },
        )
    )


async def create_provider(session: AsyncSession, **kwargs: object) -> AiProvider:
    n = next(_seq)
    provider = AiProvider(**{"name": f"Provider {n}", "adapter": "openai", **kwargs})  # type: ignore[arg-type]
    session.add(provider)
    await session.commit()
    return provider


async def create_model(
    session: AsyncSession, *, provider_id: int | None = None, **kwargs: object
) -> AiModel:
    n = next(_seq)
    if provider_id is None:
        provider_id = (await create_provider(session)).id
    model = AiModel(
        **{
            "provider_id": provider_id,
            "model_id": f"test-model-{n}",
            "display_name": f"Test Model {n}",
            "model_type": "chat",
            "origin": "manual",
            "is_enabled": True,
            **kwargs,
        }  # type: ignore[arg-type]
    )
    session.add(model)
    await session.commit()
    return model


async def create_usage(
    session: AsyncSession,
    *,
    model_id: int,
    function: AiFunction = AiFunction.CHAT,
    bucket_date: date | None = None,
    **kwargs: object,
) -> ModelUsage:
    usage = ModelUsage(
        **{
            "model_id": model_id,
            "function": function,
            "bucket_date": bucket_date or date(2026, 7, 1),
            **kwargs,
        }  # type: ignore[arg-type]
    )
    session.add(usage)
    await session.commit()
    return usage


async def get_tool(session: AsyncSession, name: str) -> Tool:
    """Fetch a seeded preset row (web_search / fetch_url)."""
    return (await session.execute(sa.select(Tool).where(Tool.name == name))).scalar_one()


async def get_builtin_model(
    session: AsyncSession, model_id: str = BUILTIN_EMBEDDING_MODEL
) -> AiModel:
    """Fetch a migration-seeded builtin catalog row."""
    return (
        await session.execute(sa.select(AiModel).where(AiModel.model_id == model_id))
    ).scalar_one()


async def allow_chat(session: AsyncSession, model_pk: int, *, default: bool = True) -> None:
    """Put a catalog model on the chat allow-list (data-model.html#t-chat-models)."""
    session.add(ChatModel(model_id=model_pk, is_default=default))
    await session.commit()


async def assign_embedding(session: AsyncSession, model_pk: int | None = None) -> AiModel:
    """Point harvester_embedding at a model (default: the seeded builtin), idempotently."""
    if model_pk is None:
        model_pk = (await get_builtin_model(session)).id
    await session.execute(sa.delete(ModelAssignment))
    session.add(ModelAssignment(function=AiFunction.HARVESTER_EMBEDDING, model_id=model_pk))
    await session.commit()
    return await session.get_one(AiModel, model_pk)
