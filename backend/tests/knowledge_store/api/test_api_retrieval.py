"""Retrieval HTTP contract: ranked lists, 422 on bad params, top-K ceiling (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import (
    AclScene,
    acl_scene,
    create_chunk,
    create_edge,
    create_entity,
    grant,
)
from tests.factories.users import create_user

from achilles.knowledge_store.constants import AclScope

pytestmark = [pytest.mark.api, pytest.mark.p1]


@pytest.fixture
async def scene(db_session: AsyncSession, authorize: AuthorizeFn) -> AclScene:
    user = await create_user(db_session)
    scene = await acl_scene(db_session, user=user)
    await authorize(user.email)
    return scene


async def granted_entity_with_text(db_session: AsyncSession, scene: AclScene, text: str) -> int:
    entity = await create_entity(db_session, source_id=scene.source.id, source_type="page")
    await create_chunk(db_session, entity_id=entity.id, text=text)
    await grant(
        db_session,
        entity_id=entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )
    return entity.id


async def test_lexical_returns_a_ranked_list(
    client: AsyncClient, db_session: AsyncSession, scene: AclScene
):
    entity_id = await granted_entity_with_text(db_session, scene, "unique kumquat report")
    resp = await client.post("/api/v1/retrieval/lexical", json={"query": "kumquat"})
    assert resp.status_code == 200
    hits = resp.json()
    assert hits[0]["entity_id"] == entity_id
    assert hits[0]["chunk_id"] is not None
    assert hits[0]["score"] > 0


async def test_empty_result_is_200_with_empty_list(client: AsyncClient, scene: AclScene):
    del scene
    resp = await client.post("/api/v1/retrieval/lexical", json={"query": "nonexistent"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_graph_walks_from_start_nodes(
    client: AsyncClient, db_session: AsyncSession, scene: AclScene
):
    a = await granted_entity_with_text(db_session, scene, "node a")
    b = await granted_entity_with_text(db_session, scene, "node b")
    await create_edge(db_session, src_entity_id=a, dst_entity_id=b)

    resp = await client.post("/api/v1/retrieval/graph", json={"start_ids": [a], "depth": 1})
    assert resp.status_code == 200
    assert [h["entity_id"] for h in resp.json()] == [b]
    assert resp.json()[0]["depth"] == 1


async def test_sql_filters_the_relational_body(
    client: AsyncClient, db_session: AsyncSession, scene: AclScene
):
    entity_id = await granted_entity_with_text(db_session, scene, "sql body")
    resp = await client.post("/api/v1/retrieval/sql", json={"source_types": ["page"]})
    assert resp.status_code == 200
    assert [h["entity_id"] for h in resp.json()] == [entity_id]

    miss = await client.post("/api/v1/retrieval/sql", json={"source_types": ["ticket"]})
    assert miss.json() == []


@pytest.mark.parametrize("depth", [0, 4])
async def test_depth_outside_bounds_is_422(client: AsyncClient, scene: AclScene, depth: int):
    del scene
    resp = await client.post("/api/v1/retrieval/graph", json={"start_ids": [1], "depth": depth})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_unknown_filter_field_is_422_not_500(client: AsyncClient, scene: AclScene):
    del scene
    resp = await client.post(
        "/api/v1/retrieval/sql",
        json={"assignee": "alice"},  # not in the closed list
    )
    assert resp.status_code == 422


async def test_over_ceiling_top_k_is_truncated_not_rejected(
    client: AsyncClient, db_session: AsyncSession, scene: AclScene
):
    await granted_entity_with_text(db_session, scene, "ceiling probe")
    resp = await client.post(
        "/api/v1/retrieval/lexical", json={"query": "ceiling", "top_k": 100000}
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 100
