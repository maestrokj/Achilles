"""Embed-on-write: upsert fills vectors best-effort, text change re-embeds (P1)."""

import json

import httpx
import pytest
import respx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.ai import BUILTIN_EMBEDDING_MODEL as MODEL
from tests.factories.ai import EMBEDDINGS_URL, assign_embedding
from tests.factories.knowledge import create_source

from achilles.ai_foundation.constants import EMBEDDING_DIM, AiFunction
from achilles.ai_foundation.models import ModelUsage
from achilles.ai_foundation.services.tokenizer import approx_counter
from achilles.knowledge_store.models import Chunk
from achilles.knowledge_store.services import embedding_write
from achilles.knowledge_store.services.entities import EntityPayload, upsert_entity

pytestmark = [pytest.mark.integration, pytest.mark.p1]


@pytest.fixture
def runtime(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    return hibp_clean


def mock_embed_any(runtime: respx.MockRouter) -> respx.Route:
    """Answer any batch with matching basis vectors and reported usage."""

    def responder(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.read())["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * (EMBEDDING_DIM - 1)}
                    for i in range(len(texts))
                ],
                "usage": {"prompt_tokens": 5 * len(texts)},
            },
        )

    return runtime.post(EMBEDDINGS_URL).mock(side_effect=responder)


def payload(source_id: int, *, body: str, native_id: str = "doc-1") -> EntityPayload:
    return EntityPayload(
        source_id=source_id,
        source_type="page",
        source_entity_id=native_id,
        title="Doc",
        body=body,
    )


async def embedded_states(session: AsyncSession, entity_id: int) -> list[str | None]:
    rows = await session.execute(
        sa.select(Chunk.embedding_model).where(Chunk.entity_id == entity_id).order_by(Chunk.ordinal)
    )
    return list(rows.scalars())


async def test_upsert_embeds_new_chunks_and_records_spend(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    source = await create_source(db_session)
    await assign_embedding(db_session)
    mock_embed_any(runtime)

    entity_id = await upsert_entity(
        db_session, payload(source.id, body="alpha beta"), token_counter=approx_counter
    )
    await db_session.commit()

    assert await embedded_states(db_session, entity_id) == [MODEL]
    usage = (
        await db_session.execute(
            sa.select(ModelUsage).where(ModelUsage.function == AiFunction.HARVESTER_EMBEDDING)
        )
    ).scalar_one()
    assert usage.input_tokens == 5


async def test_silent_runtime_leaves_null_and_the_upsert_succeeds(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    source = await create_source(db_session)
    await assign_embedding(db_session)
    runtime.post(EMBEDDINGS_URL).mock(side_effect=httpx.ConnectError("down"))

    entity_id = await upsert_entity(
        db_session, payload(source.id, body="alpha beta"), token_counter=approx_counter
    )
    await db_session.commit()

    assert await embedded_states(db_session, entity_id) == [None]


async def test_no_assignment_means_no_egress(db_session: AsyncSession, runtime: respx.MockRouter):
    source = await create_source(db_session)
    route = mock_embed_any(runtime)

    await upsert_entity(
        db_session, payload(source.id, body="alpha beta"), token_counter=approx_counter
    )
    await db_session.commit()

    assert not route.called


async def test_deferred_upsert_batches_the_page_in_one_call(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    """The Harvester path: embed_inline=False per item, one embed call per page."""
    source = await create_source(db_session)
    await assign_embedding(db_session)
    route = mock_embed_any(runtime)

    ids = [
        await upsert_entity(
            db_session,
            payload(source.id, body=f"text {n}", native_id=f"doc-{n}"),
            token_counter=approx_counter,
            embed_inline=False,
        )
        for n in (1, 2)
    ]
    assert not route.called  # deferred: no per-item egress

    await embedding_write.embed_pending(db_session, ids)
    await db_session.commit()

    assert route.call_count == 1  # both entities' chunks in a single batch
    for entity_id in ids:
        assert await embedded_states(db_session, entity_id) == [MODEL]


async def test_text_change_invalidates_and_reembeds_only_the_changed_chunk(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    source = await create_source(db_session)
    await assign_embedding(db_session)
    route = mock_embed_any(runtime)

    entity_id = await upsert_entity(
        db_session, payload(source.id, body="original text"), token_counter=approx_counter
    )
    await db_session.commit()
    assert route.call_count == 1

    # Unchanged re-capture: the valid embedding survives, no new egress.
    await upsert_entity(
        db_session, payload(source.id, body="original text"), token_counter=approx_counter
    )
    await db_session.commit()
    assert route.call_count == 1

    # Changed text: apply_diff NULLs the vector, the tail re-embeds it.
    await upsert_entity(
        db_session, payload(source.id, body="rewritten text"), token_counter=approx_counter
    )
    await db_session.commit()
    assert route.call_count == 2
    assert await embedded_states(db_session, entity_id) == [MODEL]
