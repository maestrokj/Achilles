"""External tier contract: key-only identity, scope narrows, ACL stays on top (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import AuthorizeFn
from tests.auth.integration.conftest import issue_key_only as _issue_key
from tests.factories.knowledge import create_chunk, create_entity, create_source, grant
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

SEARCH_URL = "/public/v1/search"
TEXT = "quarterly report alpha"


async def _public_scene(db_session: AsyncSession) -> tuple[int, int]:
    """Two org-public entities in two sources → (source_a_id, entity_a_id)."""
    source_a = await create_source(db_session)
    source_b = await create_source(db_session)
    entity_a = await create_entity(db_session, source_id=source_a.id, title="Alpha page")
    entity_b = await create_entity(db_session, source_id=source_b.id, title="Beta page")
    for entity in (entity_a, entity_b):
        await grant(db_session, entity_id=entity.id)
        await create_chunk(db_session, entity_id=entity.id, text=TEXT)
    return source_a.id, entity_a.id


async def test_anonymous_is_401(client: AsyncClient):
    assert (await client.post(SEARCH_URL, json={"query": "x"})).status_code == 401


async def test_jwt_does_not_cross_the_tier(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)  # a valid web session…
    resp = await client.post(SEARCH_URL, json={"query": "x"})
    assert resp.status_code == 401, "the external tier accepts API keys only"


async def test_search_shape_and_unscoped_key_sees_all(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _public_scene(db_session)
    raw_key = await _issue_key(client, db_session, authorize)

    resp = await client.post(
        SEARCH_URL,
        json={"query": TEXT},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True  # no embedder in the test DB
    assert len(body["results"]) == 2
    first = body["results"][0]
    assert set(first) == {"title", "snippet", "source", "url", "score"}
    assert first["snippet"] == TEXT
    assert first["source"] == "page"


async def test_scope_narrows_to_listed_sources(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    source_a_id, _ = await _public_scene(db_session)
    raw_key = await _issue_key(client, db_session, authorize, sources=[source_a_id])

    resp = await client.post(
        SEARCH_URL,
        json={"query": TEXT},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 200
    titles = [result["title"] for result in resp.json()["results"]]
    assert titles == ["Alpha page"]


async def test_acl_stays_on_top_of_the_scope(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    # An entity inside the scoped source but without any ACL grant stays hidden.
    source = await create_source(db_session)
    entity = await create_entity(db_session, source_id=source.id)
    await create_chunk(db_session, entity_id=entity.id, text=TEXT)
    raw_key = await _issue_key(client, db_session, authorize, sources=[source.id])

    resp = await client.post(
        SEARCH_URL,
        json={"query": TEXT},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_bad_params_answer_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    raw_key = await _issue_key(client, db_session, authorize)
    headers = {"Authorization": f"Bearer {raw_key}"}
    assert (await client.post(SEARCH_URL, json={"query": ""}, headers=headers)).status_code == 422
    assert (
        await client.post(SEARCH_URL, json={"query": "x", "limit": 0}, headers=headers)
    ).status_code == 422
    assert (
        await client.post(SEARCH_URL, json={"query": "x", "limit": 26}, headers=headers)
    ).status_code == 422


async def test_one_bucket_across_surfaces(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """A key call on /api/v1 and one on /public/v1 debit the same 60-rpm bucket."""
    raw_key = await _issue_key(client, db_session, authorize)
    headers = {"Authorization": f"Bearer {raw_key}"}

    internal = await client.get("/api/v1/api-keys", headers=headers)
    external = await client.post(SEARCH_URL, json={"query": "x"}, headers=headers)
    assert internal.status_code == 200 and external.status_code == 200
    first = int(internal.headers["X-RateLimit-Remaining"])
    second = int(external.headers["X-RateLimit-Remaining"])
    assert second == first - 1
