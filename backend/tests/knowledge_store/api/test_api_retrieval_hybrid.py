"""Vector/hybrid HTTP contract: 503 without an embedder, degraded flag, 422 (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]


@pytest.fixture
async def as_member(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    user = await create_user(db_session)
    await authorize(user.email)


async def test_vector_answers_503_while_the_embedder_is_silent(
    client: AsyncClient, as_member: None
):
    resp = await client.post("/api/v1/retrieval/vector", json={"query": "anything"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "EMBEDDINGS_UNAVAILABLE"


async def test_hybrid_degrades_to_200_on_the_same_silence(client: AsyncClient, as_member: None):
    resp = await client.post("/api/v1/retrieval/hybrid", json={"query": "anything"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"hits": [], "degraded": True}


async def test_hybrid_accepts_value_filters(client: AsyncClient, as_member: None):
    resp = await client.post(
        "/api/v1/retrieval/hybrid",
        json={"query": "x", "filters": {"source_types": ["ticket"]}},
    )
    assert resp.status_code == 200


async def test_bad_params_answer_422(client: AsyncClient, as_member: None):
    assert (await client.post("/api/v1/retrieval/vector", json={"query": ""})).status_code == 422
    assert (
        await client.post("/api/v1/retrieval/hybrid", json={"query": "x", "extra": 1})
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/retrieval/hybrid", json={"query": "x", "filters": {"nope": True}}
        )
    ).status_code == 422


async def test_anonymous_is_401(client: AsyncClient):
    assert (await client.post("/api/v1/retrieval/vector", json={"query": "x"})).status_code == 401
    assert (await client.post("/api/v1/retrieval/hybrid", json={"query": "x"})).status_code == 401
