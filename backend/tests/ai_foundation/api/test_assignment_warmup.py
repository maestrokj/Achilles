"""Assigning the embedding function warms the Platform runtime, best-effort (P1)."""

import json

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.ai import get_builtin_model

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


async def test_assignment_warms_the_runtime(
    embeddings_runtime_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    builtin = await get_builtin_model(db_session)
    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200

    load_calls = [
        call for call in embeddings_runtime_mock.calls if call.request.url.path == "/admin/load"
    ]
    assert len(load_calls) == 1
    assert json.loads(load_calls[0].request.read()) == {"model_id": "BAAI/bge-m3"}


async def test_unreachable_runtime_never_fails_the_patch(
    embeddings_runtime_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    embeddings_runtime_mock.post(url__startswith="http://embeddings").mock(
        side_effect=httpx.ConnectError("runtime down")
    )
    builtin = await get_builtin_model(db_session)

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200  # the warm-up is advice, not a dependency
    assert resp.json()["harvester_embedding"] == builtin.id
