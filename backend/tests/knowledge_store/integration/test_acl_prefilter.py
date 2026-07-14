"""ACL pre-filter inside the search SQL: lexical + sql primitives under rights (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import (
    acl_scene,
    create_chunk,
    create_entity,
    grant,
)
from tests.factories.users import create_user

from achilles.knowledge_store.constants import AclScope
from achilles.knowledge_store.models import EntityAcl
from achilles.knowledge_store.retrieval import lexical, sql
from achilles.knowledge_store.retrieval.sql import SqlFilters

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def entity_with_chunk(db_session: AsyncSession, source_id: int, text: str) -> int:
    entity = await create_entity(db_session, source_id=source_id)
    await create_chunk(db_session, entity_id=entity.id, text=text)
    return entity.id


async def test_group_grant_opens_access_only_to_members(db_session: AsyncSession):
    insider = await create_user(db_session)
    outsider = await create_user(db_session)
    scene = await acl_scene(db_session, user=insider)
    entity_id = await entity_with_chunk(db_session, scene.source.id, "quarterly synergy report")
    await grant(
        db_session,
        entity_id=entity_id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )

    mine = await lexical.search(db_session, user_id=insider.id, query="synergy", top_k=10)
    theirs = await lexical.search(db_session, user_id=outsider.id, query="synergy", top_k=10)
    assert [h.entity_id for h in mine] == [entity_id]
    assert theirs == []


async def test_direct_principal_grant_bypasses_groups(db_session: AsyncSession):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    entity_id = await entity_with_chunk(db_session, scene.source.id, "direct grant document")
    await grant(
        db_session,
        entity_id=entity_id,
        scope=AclScope.PRINCIPAL.value,
        source_principal_id=scene.principal.id,
    )

    hits = await lexical.search(db_session, user_id=user.id, query="direct", top_k=10)
    assert [h.entity_id for h in hits] == [entity_id]


async def test_public_is_visible_without_any_membership(db_session: AsyncSession):
    stranger = await create_user(db_session)  # no identity, no principals at all
    scene_owner = await create_user(db_session)
    scene = await acl_scene(db_session, user=scene_owner)
    entity_id = await entity_with_chunk(db_session, scene.source.id, "public announcement")
    await grant(db_session, entity_id=entity_id, scope=AclScope.PUBLIC.value)

    hits = await lexical.search(db_session, user_id=stranger.id, query="announcement", top_k=10)
    assert [h.entity_id for h in hits] == [entity_id]


async def test_chunk_inherits_the_parent_entity_acl(db_session: AsyncSession):
    """Every chunk of a granted entity is reachable; chunks carry no own grants."""
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=entity.id, ordinal=0, text="fragment apple one")
    await create_chunk(db_session, entity_id=entity.id, ordinal=1, text="fragment apple two")
    await grant(
        db_session,
        entity_id=entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )

    hits = await lexical.search(db_session, user_id=user.id, query="apple", top_k=10)
    assert {h.entity_id for h in hits} == {entity.id}
    assert len(hits) == 2


async def test_revocation_takes_effect_immediately_after_sync(db_session: AsyncSession):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    entity_id = await entity_with_chunk(db_session, scene.source.id, "revocable content")
    await grant(
        db_session,
        entity_id=entity_id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )
    assert await lexical.search(db_session, user_id=user.id, query="revocable", top_k=10)

    await db_session.execute(sa.delete(EntityAcl).where(EntityAcl.entity_id == entity_id))
    await db_session.commit()
    assert await lexical.search(db_session, user_id=user.id, query="revocable", top_k=10) == []


async def test_empty_access_is_an_empty_list_not_an_error(db_session: AsyncSession):
    user = await create_user(db_session)
    assert await lexical.search(db_session, user_id=user.id, query="anything", top_k=10) == []
    assert await sql.search(db_session, user_id=user.id, filters=SqlFilters(), top_k=10) == []


async def test_sql_primitive_applies_the_same_prefilter(db_session: AsyncSession):
    user = await create_user(db_session)
    outsider = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    granted = await create_entity(db_session, source_id=scene.source.id, source_type="ticket")
    hidden = await create_entity(db_session, source_id=scene.source.id, source_type="ticket")
    await grant(
        db_session,
        entity_id=granted.id,
        scope=AclScope.PRINCIPAL.value,
        source_principal_id=scene.principal.id,
    )
    del hidden

    filters = SqlFilters(source_types=("ticket",))
    mine = await sql.search(db_session, user_id=user.id, filters=filters, top_k=10)
    theirs = await sql.search(db_session, user_id=outsider.id, filters=filters, top_k=10)
    assert [h.entity_id for h in mine] == [granted.id]
    assert theirs == []


async def test_scope_field_consistency_is_enforced_by_the_db(db_session: AsyncSession):
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    # Plain ints: rollback expires ORM instances, and a lazy refresh would need IO.
    entity_id = (await create_entity(db_session, source_id=scene.source.id)).id
    group_id = scene.group.id

    with pytest.raises(IntegrityError):
        await grant(db_session, entity_id=entity_id, scope=AclScope.GROUP.value)  # no group set
    await db_session.rollback()

    with pytest.raises(IntegrityError):
        await grant(
            db_session,
            entity_id=entity_id,
            scope=AclScope.PUBLIC.value,
            source_group_id=group_id,  # public must not carry a recipient
        )
    await db_session.rollback()
