"""Assembled hybrid: fusion across primitives, degradation, hidden-ACL hint (P0)."""

import httpx
import pytest
import respx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.ai import (
    BUILTIN_EMBEDDING_MODEL as MODEL,
)
from tests.factories.ai import (
    EMBEDDINGS_URL,
    assign_embedding,
    basis,
    mock_embed,
)
from tests.factories.knowledge import (
    acl_scene,
    create_chunk,
    create_edge,
    create_entity,
    create_identity,
    create_principal,
    grant,
)
from tests.factories.users import create_user

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import ModelUsage
from achilles.knowledge_store.constants import AclScope
from achilles.knowledge_store.retrieval import hybrid
from achilles.knowledge_store.retrieval.sql import SqlFilters

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
def runtime(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    """The egress guard doubles as the embeddings runtime mock."""
    return hibp_clean


async def test_entity_hit_by_both_primitives_ranks_first(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    mock_embed(runtime, basis(0))

    async def entity_with(text: str, embedding: list[float] | None) -> int:
        entity = await create_entity(db_session, source_id=scene.source.id)
        await create_chunk(
            db_session,
            entity_id=entity.id,
            text=text,
            embedding=embedding,
            embedding_model=MODEL if embedding else None,
        )
        await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)
        return entity.id

    both = await entity_with("release checklist for deploy", basis(0))
    lexical_only = await entity_with("release notes archive", None)
    vector_only = await entity_with("unrelated wording entirely", basis(2))

    result = await hybrid.search(db_session, user_id=user.id, query="release", top_k=10)

    assert result.degraded is False
    assert result.hits[0].entity_id == both
    assert {hit.entity_id for hit in result.hits} >= {both, lexical_only, vector_only}
    assert result.hits[0].best_chunk_id is not None


async def test_silent_embedder_degrades_to_text_lists(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    runtime.post(EMBEDDINGS_URL).mock(side_effect=httpx.ConnectError("runtime down"))
    entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=entity.id, text="degradation drill")
    await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)

    result = await hybrid.search(db_session, user_id=user.id, query="degradation", top_k=10)

    assert result.degraded is True
    assert [hit.entity_id for hit in result.hits] == [entity.id]


async def test_no_assignment_degrades_without_any_egress(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)

    result = await hybrid.search(db_session, user_id=user.id, query="anything", top_k=10)

    assert result.degraded is True
    assert result.hits == []
    embed_calls = [c for c in runtime.calls if c.request.url.path == "/v1/embeddings"]
    assert embed_calls == []


async def test_graph_neighbourhood_joins_the_fusion(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    mock_embed(runtime, basis(0))

    anchor = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=anchor.id, text="incident postmortem")
    await grant(db_session, entity_id=anchor.id, scope=AclScope.PUBLIC.value)
    neighbour = await create_entity(db_session, source_id=scene.source.id)
    await grant(db_session, entity_id=neighbour.id, scope=AclScope.PUBLIC.value)
    await create_edge(db_session, src_entity_id=anchor.id, dst_entity_id=neighbour.id)

    result = await hybrid.search(db_session, user_id=user.id, query="postmortem", top_k=10)

    ids = [hit.entity_id for hit in result.hits]
    assert anchor.id in ids
    assert neighbour.id in ids  # one hop from the top text hit


async def test_value_filters_engage_the_sql_primitive(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    mock_embed(runtime, basis(0))

    ticket = await create_entity(db_session, source_id=scene.source.id, source_type="ticket")
    await grant(db_session, entity_id=ticket.id, scope=AclScope.PUBLIC.value)

    result = await hybrid.search(
        db_session,
        user_id=user.id,
        query="nothing textual matches this",
        top_k=10,
        filters=SqlFilters(source_types=("ticket",)),
    )

    assert ticket.id in {hit.entity_id for hit in result.hits}


async def test_hidden_top_candidate_yields_a_content_free_hint(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    mock_embed(runtime, basis(0))

    author_identity = await create_identity(db_session, email="owner@corp.test")
    author = await create_principal(
        db_session, source_id=scene.source.id, identity_id=author_identity.id
    )
    hidden = await create_entity(
        db_session,
        source_id=scene.source.id,
        source_type="page",
        author_principal_id=author.id,
    )
    await create_chunk(db_session, entity_id=hidden.id, text="secret roadmap details")
    # No grant for our user; someone else holds it.
    other = await create_user(db_session)
    other_scene = await acl_scene(db_session, user=other)
    await grant(
        db_session,
        entity_id=hidden.id,
        scope=AclScope.GROUP.value,
        source_group_id=other_scene.group.id,
    )

    result = await hybrid.search(db_session, user_id=user.id, query="roadmap", top_k=10)
    assert result.hits == []

    hint = await hybrid.hidden_hint(
        db_session,
        user_id=user.id,
        query="roadmap",
        query_vector=result.query_vector,
        embedding_model=result.embedding_model,
    )
    assert hint is not None
    assert hint.source_type == "page"
    assert hint.author_email == "owner@corp.test"


async def test_fully_visible_top_carries_no_hint(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await assign_embedding(db_session)
    mock_embed(runtime, basis(0))
    entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=entity.id, text="visible manual")
    await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)

    result = await hybrid.search(db_session, user_id=user.id, query="manual", top_k=10)
    hint = await hybrid.hidden_hint(
        db_session,
        user_id=user.id,
        query="manual",
        query_vector=result.query_vector,
        embedding_model=result.embedding_model,
    )

    assert hint is None


async def test_query_embedding_spend_lands_in_query_rag(
    db_session: AsyncSession, runtime: respx.MockRouter
):
    user = await create_user(db_session)
    model = await assign_embedding(db_session)
    mock_embed(runtime, basis(0))

    await hybrid.search(db_session, user_id=user.id, query="spend probe", top_k=10)

    row = (
        await db_session.execute(
            sa.select(ModelUsage).where(ModelUsage.function == AiFunction.QUERY_RAG)
        )
    ).scalar_one()
    assert row.model_id == model.id
    assert row.input_tokens == 7  # runtime-reported prompt_tokens
    assert row.request_count == 1
