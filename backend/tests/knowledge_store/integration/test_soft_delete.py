"""Soft delete: a separate axis from status, hides from every primitive (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import (
    AclScene,
    acl_scene,
    create_chunk,
    create_edge,
    create_entity,
    grant,
)
from tests.factories.users import create_user

from achilles.auth.models import User
from achilles.knowledge_store.constants import AclScope, EntityStatus
from achilles.knowledge_store.models import Chunk, Entity
from achilles.knowledge_store.repositories import entities as entities_repo
from achilles.knowledge_store.retrieval import graph, lexical, sql
from achilles.knowledge_store.retrieval.sql import SqlFilters
from achilles.knowledge_store.services import emptiness
from achilles.knowledge_store.services.entities import (
    AclDraft,
    EntityPayload,
    restore,
    soft_delete,
    upsert_entity,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture
async def user(db_session: AsyncSession) -> User:
    return await create_user(db_session)


@pytest.fixture
async def scene(db_session: AsyncSession, user: User) -> AclScene:
    return await acl_scene(db_session, user=user)


async def make_visible_entity(
    db_session: AsyncSession, scene: AclScene, *, text: str = "searchable haystack"
) -> int:
    entity = await create_entity(
        db_session, source_id=scene.source.id, status=EntityStatus.FINAL.value
    )
    await create_chunk(db_session, entity_id=entity.id, text=text)
    await grant(
        db_session,
        entity_id=entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )
    return entity.id


async def test_soft_delete_hides_from_every_primitive(
    db_session: AsyncSession, user: User, scene: AclScene
):
    start_id = await make_visible_entity(db_session, scene, text="start node")
    entity_id = await make_visible_entity(db_session, scene)
    await create_edge(db_session, src_entity_id=start_id, dst_entity_id=entity_id)

    await soft_delete(db_session, entity_id)
    await db_session.commit()

    assert await lexical.search(db_session, user_id=user.id, query="haystack", top_k=10) == []
    sql_hits = await sql.search(db_session, user_id=user.id, filters=SqlFilters(), top_k=10)
    assert entity_id not in {h.entity_id for h in sql_hits}
    graph_hits = await graph.search(
        db_session, user_id=user.id, start_ids=[start_id], depth=1, top_k=10
    )
    assert graph_hits == []


async def test_mirror_flag_lands_on_chunks_in_the_same_transaction(
    db_session: AsyncSession, scene: AclScene
):
    entity_id = await make_visible_entity(db_session, scene)
    await soft_delete(db_session, entity_id)  # not committed yet

    flags = (
        (await db_session.execute(sa.select(Chunk.is_deleted).where(Chunk.entity_id == entity_id)))
        .scalars()
        .all()
    )
    assert flags == [True]
    await db_session.rollback()


async def test_status_is_untouched_by_the_delete_axis(db_session: AsyncSession, scene: AclScene):
    entity_id = await make_visible_entity(db_session, scene)
    await soft_delete(db_session, entity_id)
    await db_session.commit()

    entity = await db_session.get(Entity, entity_id)
    assert entity is not None
    assert entity.status == EntityStatus.FINAL.value
    assert entity.is_deleted is True


async def test_restore_brings_the_entity_back(
    db_session: AsyncSession, user: User, scene: AclScene
):
    entity_id = await make_visible_entity(db_session, scene)
    await soft_delete(db_session, entity_id)
    await db_session.commit()

    await restore(db_session, entity_id)
    await db_session.commit()

    hits = await lexical.search(db_session, user_id=user.id, query="haystack", top_k=10)
    assert [h.entity_id for h in hits] == [entity_id]
    entity = await db_session.get(Entity, entity_id)
    assert entity is not None
    assert entity.deleted_at is None


async def test_recapture_of_unchanged_content_revives_the_chunks(
    db_session: AsyncSession, user: User, scene: AclScene
):
    """Re-capture revives every projection: an identical body (content_hash diff
    skips the chunks) must still clear the mirrored is_deleted."""
    payload = EntityPayload(
        source_id=scene.source.id,
        source_type="issue",
        source_entity_id="REV-1",
        body="phoenix content, byte for byte the same",
        status=EntityStatus.FINAL.value,
        acl=(AclDraft(scope=AclScope.GROUP.value, source_group_id=scene.group.id),),
    )
    entity_id = await upsert_entity(db_session, payload)
    await db_session.commit()
    await soft_delete(db_session, entity_id)
    await db_session.commit()

    assert await upsert_entity(db_session, payload) == entity_id  # same natural key
    await db_session.commit()

    flags = (
        (await db_session.execute(sa.select(Chunk.is_deleted).where(Chunk.entity_id == entity_id)))
        .scalars()
        .all()
    )
    assert flags == [False]
    hits = await lexical.search(db_session, user_id=user.id, query="phoenix", top_k=10)
    assert [h.entity_id for h in hits] == [entity_id]
    assert await emptiness.is_empty(db_session) is False


async def test_source_counters_skip_deleted_chunks(db_session: AsyncSession, scene: AclScene):
    """The admin slice must agree with emptiness/lexical: deleted chunks are not live."""
    entity_id = await make_visible_entity(db_session, scene)
    counts = await entities_repo.counts_by_source(db_session)
    assert counts[scene.source.id] == (1, 1)

    await db_session.execute(
        sa.update(Chunk).where(Chunk.entity_id == entity_id).values(is_deleted=True)
    )
    await db_session.commit()

    counts = await entities_repo.counts_by_source(db_session)
    assert counts[scene.source.id] == (1, 0)  # live entity, no live chunks


async def test_emptiness_counts_only_live_chunks(db_session: AsyncSession, scene: AclScene):
    assert await emptiness.is_empty(db_session) is True
    entity_id = await make_visible_entity(db_session, scene)
    assert await emptiness.is_empty(db_session) is False

    await soft_delete(db_session, entity_id)
    await db_session.commit()
    assert await emptiness.is_empty(db_session) is True
