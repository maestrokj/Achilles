"""API-key scope restriction inside every primitive's SQL — not a post-filter (integration)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import (
    create_chunk,
    create_edge,
    create_entity,
    create_source,
    grant,
)
from tests.factories.users import create_user

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.knowledge_store.retrieval import graph, hybrid, lexical, sql, vector

pytestmark = [pytest.mark.integration, pytest.mark.p1]

TEXT = "quarterly report alpha"
MODEL = "test-embedder"


async def _scene(session: AsyncSession) -> tuple[int, int, int]:
    """Two public entities in two sources, same text/embedding.

    Returns (user_id, source_a_id, entity_a_id); b is implied by "the other one".
    """
    user = await create_user(session)
    source_a = await create_source(session)
    source_b = await create_source(session)
    entity_a = await create_entity(session, source_id=source_a.id)
    entity_b = await create_entity(session, source_id=source_b.id)
    for entity in (entity_a, entity_b):
        await grant(session, entity_id=entity.id)
        await create_chunk(
            session,
            entity_id=entity.id,
            text=TEXT,
            embedding=[0.1] * EMBEDDING_DIM,
            embedding_model=MODEL,
        )
    return user.id, source_a.id, entity_a.id  # b is implied by "the other one"


async def test_lexical_restricts_to_allowed_sources(db_session: AsyncSession):
    user_id, source_a_id, entity_a_id = await _scene(db_session)
    unrestricted = await lexical.search(db_session, user_id=user_id, query=TEXT, top_k=10)
    assert len(unrestricted) == 2
    scoped = await lexical.search(
        db_session, user_id=user_id, query=TEXT, top_k=10, allowed_source_ids=[source_a_id]
    )
    assert [hit.entity_id for hit in scoped] == [entity_a_id]


async def test_vector_restricts_to_allowed_sources(db_session: AsyncSession):
    user_id, source_a_id, entity_a_id = await _scene(db_session)
    query_vector = [0.1] * EMBEDDING_DIM
    unrestricted = await vector.search(
        db_session, user_id=user_id, query_vector=query_vector, embedding_model=MODEL, top_k=10
    )
    assert len(unrestricted) == 2
    scoped = await vector.search(
        db_session,
        user_id=user_id,
        query_vector=query_vector,
        embedding_model=MODEL,
        top_k=10,
        allowed_source_ids=[source_a_id],
    )
    assert [hit.entity_id for hit in scoped] == [entity_a_id]


async def test_sql_restricts_to_allowed_sources(db_session: AsyncSession):
    user_id, source_a_id, entity_a_id = await _scene(db_session)
    filters = sql.SqlFilters(source_types=["page"])
    unrestricted = await sql.search(db_session, user_id=user_id, filters=filters, top_k=10)
    assert len(unrestricted) == 2
    scoped = await sql.search(
        db_session,
        user_id=user_id,
        filters=filters,
        top_k=10,
        allowed_source_ids=[source_a_id],
    )
    assert [hit.entity_id for hit in scoped] == [entity_a_id]


async def test_graph_scope_breaks_the_path_like_acl(db_session: AsyncSession):
    user_id, source_a_id, entity_a_id = await _scene(db_session)
    # entity_a → entity_b: the neighbour lives in the out-of-scope source.
    entity_b_id = next(
        hit.entity_id
        for hit in await lexical.search(db_session, user_id=user_id, query=TEXT, top_k=10)
        if hit.entity_id != entity_a_id
    )
    await create_edge(db_session, src_entity_id=entity_a_id, dst_entity_id=entity_b_id)

    unrestricted = await graph.search(
        db_session, user_id=user_id, start_ids=[entity_a_id], depth=1, top_k=10
    )
    assert [hit.entity_id for hit in unrestricted] == [entity_b_id]
    scoped = await graph.search(
        db_session,
        user_id=user_id,
        start_ids=[entity_a_id],
        depth=1,
        top_k=10,
        allowed_source_ids=[source_a_id],
    )
    assert scoped == []


async def test_hybrid_passes_the_scope_through(db_session: AsyncSession):
    # No embedding assignment in the test DB → the embedder is silent → the
    # fused list is lexical-only, which is enough to see the restriction land.
    user_id, source_a_id, entity_a_id = await _scene(db_session)
    result = await hybrid.search(
        db_session,
        user_id=user_id,
        query=TEXT,
        top_k=10,
        allowed_source_ids=[source_a_id],
    )
    assert result.degraded is True
    assert [hit.entity_id for hit in result.hits] == [entity_a_id]
