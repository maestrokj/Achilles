"""Graph traversal over entity_edge under the same ACL JOIN (P0)."""

import itertools

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import AclScene, acl_scene, create_edge, create_entity, grant
from tests.factories.users import create_user

from achilles.auth.models import User
from achilles.knowledge_store.constants import AclScope, RelType
from achilles.knowledge_store.retrieval import graph

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def granted_entity(db_session: AsyncSession, scene: AclScene) -> int:
    entity = await create_entity(db_session, source_id=scene.source.id)
    await grant(
        db_session,
        entity_id=entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )
    return entity.id


async def chain(db_session: AsyncSession, scene: AclScene, length: int) -> list[int]:
    """a → b → c → … — every node granted to the scene's group."""
    ids = [await granted_entity(db_session, scene) for _ in range(length)]
    for src, dst in itertools.pairwise(ids):
        await create_edge(db_session, src_entity_id=src, dst_entity_id=dst)
    return ids


@pytest.fixture
async def user(db_session: AsyncSession) -> User:
    return await create_user(db_session)


@pytest.fixture
async def scene(db_session: AsyncSession, user: User) -> AclScene:
    return await acl_scene(db_session, user=user)


async def test_depth_bounds_the_walk(db_session: AsyncSession, user: User, scene: AclScene):
    a, b, c, d = await chain(db_session, scene, 4)

    one_hop = await graph.search(db_session, user_id=user.id, start_ids=[a], depth=1, top_k=10)
    assert {h.entity_id for h in one_hop} == {b}

    three_hops = await graph.search(db_session, user_id=user.id, start_ids=[a], depth=3, top_k=10)
    assert {h.entity_id for h in three_hops} == {b, c, d}
    by_id = {h.entity_id: h for h in three_hops}
    assert by_id[b].depth == 1
    assert by_id[d].depth == 3
    assert by_id[b].score > by_id[d].score  # nearer context ranks higher


async def test_denied_node_breaks_the_path(db_session: AsyncSession, user: User, scene: AclScene):
    """ACL sits inside the recursive step: no transit through an invisible node."""
    a = await granted_entity(db_session, scene)
    b = await create_entity(db_session, source_id=scene.source.id)  # no grant at all
    c = await granted_entity(db_session, scene)
    await create_edge(db_session, src_entity_id=a, dst_entity_id=b.id)
    await create_edge(db_session, src_entity_id=b.id, dst_entity_id=c)

    hits = await graph.search(db_session, user_id=user.id, start_ids=[a], depth=3, top_k=10)
    assert hits == []  # b is invisible and c is unreachable through it


async def test_real_cycle_does_not_loop_the_cte(
    db_session: AsyncSession, user: User, scene: AclScene
):
    a, b = await chain(db_session, scene, 2)
    await create_edge(db_session, src_entity_id=b, dst_entity_id=a)  # b → a closes the cycle

    hits = await graph.search(db_session, user_id=user.id, start_ids=[a], depth=3, top_k=10)
    assert {h.entity_id for h in hits} == {b}


async def test_rel_type_filters_the_hop(db_session: AsyncSession, user: User, scene: AclScene):
    a = await granted_entity(db_session, scene)
    linked = await granted_entity(db_session, scene)
    mentioned = await granted_entity(db_session, scene)
    await create_edge(
        db_session, src_entity_id=a, dst_entity_id=linked, rel_type=RelType.LINKS_TO.value
    )
    await create_edge(
        db_session, src_entity_id=a, dst_entity_id=mentioned, rel_type=RelType.MENTIONS.value
    )

    hits = await graph.search(
        db_session,
        user_id=user.id,
        start_ids=[a],
        depth=1,
        rel_types=[RelType.MENTIONS.value],
        top_k=10,
    )
    assert {h.entity_id for h in hits} == {mentioned}


async def test_weight_threshold_cuts_the_hop_before_acl(
    db_session: AsyncSession, user: User, scene: AclScene
):
    """The weak edge is cut by the width bound even though its target is accessible."""
    a = await granted_entity(db_session, scene)
    strong = await granted_entity(db_session, scene)
    weak = await granted_entity(db_session, scene)
    await create_edge(db_session, src_entity_id=a, dst_entity_id=strong, weight=0.9)
    await create_edge(db_session, src_entity_id=a, dst_entity_id=weak, weight=0.1)

    hits = await graph.search(
        db_session, user_id=user.id, start_ids=[a], depth=1, weight_min=0.5, top_k=10
    )
    assert {h.entity_id for h in hits} == {strong}


async def test_unreadable_start_node_yields_nothing(
    db_session: AsyncSession, user: User, scene: AclScene
):
    hidden_start = await create_entity(db_session, source_id=scene.source.id)  # no grant
    reachable = await granted_entity(db_session, scene)
    await create_edge(db_session, src_entity_id=hidden_start.id, dst_entity_id=reachable)

    hits = await graph.search(
        db_session, user_id=user.id, start_ids=[hidden_start.id], depth=1, top_k=10
    )
    assert hits == []
