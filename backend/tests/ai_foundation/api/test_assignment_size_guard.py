"""Assignment memory preflight + the embedder status endpoint (API).

The PATCH asks the built-in runtime whether the model fits its memory budget
before anything commits; GET /admin/ai/embedder is the Admin screens' view of
the load phase. Both degrade when the runtime is silent — a dead container
must not brick model management.
"""

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.ai import (
    EMBEDDINGS_PREFLIGHT_URL,
    EMBEDDINGS_STATUS_URL,
    create_model,
    create_provider,
    get_builtin_model,
)

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


async def test_too_large_model_is_409_and_nothing_commits(
    client: AsyncClient,
    db_session: AsyncSession,
    embeddings_runtime_mock: respx.MockRouter,
    as_admin: None,
):
    def by_path(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/preflight":
            return httpx.Response(
                200, json={"fits": False, "required_bytes": 9_000_000_000, "budget_bytes": 5}
            )
        return httpx.Response(200, json={"status": "ok"})

    embeddings_runtime_mock.post(url__startswith="http://embeddings").mock(side_effect=by_path)
    builtin = await get_builtin_model(db_session)

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})

    assert resp.status_code == 409
    assert resp.json()["code"] == "MODEL_TOO_LARGE"
    current = await client.get(f"{BASE}/assignments")
    assert current.json()["harvester_embedding"] is None  # rejected before commit


async def test_unreachable_preflight_does_not_block_assignment(
    client: AsyncClient,
    db_session: AsyncSession,
    embeddings_runtime_mock: respx.MockRouter,
    as_admin: None,
):
    embeddings_runtime_mock.post(url__startswith="http://embeddings").mock(
        side_effect=httpx.ConnectError("down")
    )
    builtin = await get_builtin_model(db_session)

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})

    assert resp.status_code == 200
    assert resp.json()["harvester_embedding"] == builtin.id


async def test_cloud_provider_skips_preflight(
    client: AsyncClient,
    db_session: AsyncSession,
    embeddings_runtime_mock: respx.MockRouter,
    as_admin: None,
):
    """Only the system provider has a runtime to ask about memory."""
    provider = await create_provider(db_session)
    model = await create_model(
        db_session,
        provider_id=provider.id,
        model_type="embedding",
        meta={"embedding_dim": 1024},
    )

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": model.id})

    assert resp.status_code == 200
    preflights = [
        call
        for call in embeddings_runtime_mock.calls
        if call.request.url == EMBEDDINGS_PREFLIGHT_URL
    ]
    assert preflights == []


# --- GET /admin/ai/embedder ---


async def test_embedder_status_empty_when_nothing_assigned(client: AsyncClient, as_admin: None):
    resp = await client.get(f"{BASE}/embedder")
    assert resp.status_code == 200
    assert resp.json() == {"assigned": None, "runtime": None}


async def test_embedder_status_reports_loading_phase(
    client: AsyncClient,
    db_session: AsyncSession,
    embeddings_runtime_mock: respx.MockRouter,
    as_admin: None,
):
    builtin = await get_builtin_model(db_session)
    await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    embeddings_runtime_mock.get(EMBEDDINGS_STATUS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "budget_bytes": 1,
                "desired": builtin.model_id,
                "models": {builtin.model_id: {"state": "loading", "error": None}},
            },
        )
    )

    payload = (await client.get(f"{BASE}/embedder")).json()

    assert payload["assigned"]["model_id"] == builtin.model_id
    assert payload["assigned"]["model_pk"] == builtin.id
    assert payload["runtime"] == {"state": "loading", "error": None}


async def test_embedder_status_unreachable_runtime(
    client: AsyncClient,
    db_session: AsyncSession,
    embeddings_runtime_mock: respx.MockRouter,
    as_admin: None,
):
    builtin = await get_builtin_model(db_session)
    await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    embeddings_runtime_mock.get(EMBEDDINGS_STATUS_URL).mock(side_effect=httpx.ConnectError("down"))

    payload = (await client.get(f"{BASE}/embedder")).json()

    assert payload["runtime"] == {"state": "unreachable", "error": None}
