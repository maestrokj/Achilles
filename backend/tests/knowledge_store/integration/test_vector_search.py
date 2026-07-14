"""Vector primitive: halfvec roundtrip, cosine ranking, ACL matrix, local quals (P0)."""

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.ai import BUILTIN_EMBEDDING_MODEL as MODEL
from tests.factories.ai import basis
from tests.factories.knowledge import acl_scene, create_chunk, create_entity, grant
from tests.factories.users import create_user

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.knowledge_store.constants import AclScope
from achilles.knowledge_store.models import Chunk
from achilles.knowledge_store.retrieval import vector

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def granted_chunk(
    db_session: AsyncSession,
    *,
    source_id: int,
    group_id: int,
    embedding: list[float],
    embedding_model: str = MODEL,
    **chunk_kwargs: object,
) -> tuple[int, int]:
    entity = await create_entity(db_session, source_id=source_id)
    chunk = await create_chunk(
        db_session,
        entity_id=entity.id,
        embedding=embedding,
        embedding_model=embedding_model,
        **chunk_kwargs,  # type: ignore[arg-type]
    )
    await grant(
        db_session, entity_id=entity.id, scope=AclScope.GROUP.value, source_group_id=group_id
    )
    return entity.id, chunk.id


async def test_halfvec_roundtrip_preserves_the_vector(db_session: AsyncSession):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=entity.id, embedding=basis(3), embedding_model=MODEL)

    stored: Any = (await db_session.execute(sa.select(Chunk.embedding))).scalar_one()
    values = stored.to_list() if hasattr(stored, "to_list") else list(stored)
    assert len(values) == EMBEDDING_DIM
    assert values[3] == pytest.approx(1.0)
    assert sum(values) == pytest.approx(1.0)


async def test_ranking_follows_cosine_proximity(db_session: AsyncSession):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    near, _ = await granted_chunk(
        db_session, source_id=scene.source.id, group_id=scene.group.id, embedding=basis(0)
    )
    far, _ = await granted_chunk(
        db_session, source_id=scene.source.id, group_id=scene.group.id, embedding=basis(1)
    )

    hits = await vector.search(
        db_session, user_id=user.id, query_vector=basis(0), embedding_model=MODEL, top_k=10
    )

    assert [hit.entity_id for hit in hits] == [near, far]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].score > hits[1].score
    assert hits[0].chunk_id is not None


async def test_acl_prefilter_composes_into_the_vector_query(db_session: AsyncSession):
    insider = await create_user(db_session)
    outsider = await create_user(db_session)
    scene = await acl_scene(db_session, user=insider)
    entity_id, _ = await granted_chunk(
        db_session, source_id=scene.source.id, group_id=scene.group.id, embedding=basis(0)
    )

    mine = await vector.search(
        db_session, user_id=insider.id, query_vector=basis(0), embedding_model=MODEL, top_k=10
    )
    theirs = await vector.search(
        db_session, user_id=outsider.id, query_vector=basis(0), embedding_model=MODEL, top_k=10
    )

    assert [hit.entity_id for hit in mine] == [entity_id]
    assert theirs == []


async def test_public_grant_reaches_a_stranger(db_session: AsyncSession):
    stranger = await create_user(db_session)
    owner = await create_user(db_session)
    scene = await acl_scene(db_session, user=owner)
    entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=entity.id, embedding=basis(0), embedding_model=MODEL)
    await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)

    hits = await vector.search(
        db_session, user_id=stranger.id, query_vector=basis(0), embedding_model=MODEL, top_k=10
    )
    assert [hit.entity_id for hit in hits] == [entity.id]


async def test_local_quals_exclude_deleted_alien_and_unembedded(db_session: AsyncSession):
    """is_deleted, another model's vectors and NULL embeddings never surface."""
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    visible, _ = await granted_chunk(
        db_session, source_id=scene.source.id, group_id=scene.group.id, embedding=basis(0)
    )
    deleted_entity, deleted_chunk = await granted_chunk(
        db_session, source_id=scene.source.id, group_id=scene.group.id, embedding=basis(0)
    )
    await db_session.execute(
        sa.update(Chunk).where(Chunk.id == deleted_chunk).values(is_deleted=True)
    )
    await db_session.commit()
    await granted_chunk(
        db_session,
        source_id=scene.source.id,
        group_id=scene.group.id,
        embedding=basis(0),
        embedding_model="alien/model",
    )
    plain_entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=plain_entity.id)  # NULL embedding
    await grant(
        db_session,
        entity_id=plain_entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )

    hits = await vector.search(
        db_session, user_id=user.id, query_vector=basis(0), embedding_model=MODEL, top_k=10
    )
    assert [hit.entity_id for hit in hits] == [visible]
    assert deleted_entity not in {hit.entity_id for hit in hits}
