"""Retrieval access: guard on every route, two users see different results (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import acl_scene, create_chunk, create_entity, grant
from tests.factories.users import create_user

from achilles.knowledge_store.constants import AclScope

pytestmark = [pytest.mark.api, pytest.mark.p1]

RETRIEVAL_CALLS = [
    ("/api/v1/retrieval/lexical", {"query": "anything"}),
    ("/api/v1/retrieval/graph", {"start_ids": [1], "depth": 1}),
    ("/api/v1/retrieval/sql", {}),
]


@pytest.mark.parametrize(("url", "body"), RETRIEVAL_CALLS)
async def test_anonymous_is_401(client: AsyncClient, url: str, body: dict[str, object]):
    resp = await client.post(url, json=body)
    assert resp.status_code == 401


async def test_two_users_see_different_results_for_the_same_query(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    insider = await create_user(db_session)
    outsider = await create_user(db_session)
    scene = await acl_scene(db_session, user=insider)

    private_entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=private_entity.id, text="tangerine secret plan")
    await grant(
        db_session,
        entity_id=private_entity.id,
        scope=AclScope.GROUP.value,
        source_group_id=scene.group.id,
    )
    public_entity = await create_entity(db_session, source_id=scene.source.id)
    await create_chunk(db_session, entity_id=public_entity.id, text="tangerine public note")
    await grant(db_session, entity_id=public_entity.id, scope=AclScope.PUBLIC.value)

    await authorize(insider.email)
    insider_hits = await client.post("/api/v1/retrieval/lexical", json={"query": "tangerine"})
    assert {h["entity_id"] for h in insider_hits.json()} == {private_entity.id, public_entity.id}

    await authorize(outsider.email)
    outsider_hits = await client.post("/api/v1/retrieval/lexical", json={"query": "tangerine"})
    assert {h["entity_id"] for h in outsider_hits.json()} == {public_entity.id}
