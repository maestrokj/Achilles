"""HTTP contract: admin endpoints, OpenAI-compatible surface, healthz."""

import httpx
import pytest

import app.main as main_module
from app.memory import ModelEstimate

from .conftest import make_manager, wait_state

EST = ModelEstimate(steady_bytes=30, peak_bytes=60)


@pytest.fixture
def manager(monkeypatch):
    fresh = make_manager(headroom=100)
    monkeypatch.setattr(main_module, "manager", fresh)
    return fresh


@pytest.fixture
async def client(manager):
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_load_then_status_and_models(client, manager, sizes):
    sizes["a"] = EST
    response = await client.post("/admin/load", json={"model_id": "a"})
    assert response.status_code == 200
    assert response.json() == {"model_id": "a", "status": "loading"}
    await wait_state(manager, "a", "ready")

    status = (await client.get("/admin/status")).json()
    assert status["desired"] == "a"
    assert status["models"]["a"] == {"state": "ready", "error": None}
    assert status["budget_bytes"] == manager.budget_bytes

    models = (await client.get("/v1/models")).json()
    assert models["data"] == [{"object": "model", "id": "a"}]


async def test_load_rejects_too_large_with_structured_409(client, sizes):
    sizes["huge"] = ModelEstimate(steady_bytes=90, peak_bytes=180)
    response = await client.post("/admin/load", json={"model_id": "huge"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "MODEL_TOO_LARGE"
    assert detail["required_bytes"] > detail["budget_bytes"]


async def test_preflight_is_a_pure_read(client, sizes):
    sizes["huge"] = ModelEstimate(steady_bytes=90, peak_bytes=180)
    response = await client.post("/admin/preflight", json={"model_id": "huge"})
    assert response.status_code == 200
    body = response.json()
    assert body["fits"] is False
    assert body["required_bytes"] > body["budget_bytes"]
    assert (await client.get("/admin/status")).json()["models"] == {}


async def test_preflight_unknown_size_defaults_to_fits(client, sizes):
    body = (await client.post("/admin/preflight", json={"model_id": "mystery"})).json()
    assert body["fits"] is True
    assert body["required_bytes"] is None


async def test_embeddings_503_while_not_loaded(client, sizes):
    response = await client.post("/v1/embeddings", json={"model": "a", "input": "hi"})
    assert response.status_code == 503
    assert "not_loaded" in response.json()["detail"]


async def test_embeddings_roundtrip(client, manager, sizes):
    sizes["a"] = EST
    await client.post("/admin/load", json={"model_id": "a"})
    await wait_state(manager, "a", "ready")
    response = await client.post(
        "/v1/embeddings", json={"model": "a", "input": ["x", "y"]}
    )
    assert response.status_code == 200
    payload = response.json()
    assert [item["index"] for item in payload["data"]] == [0, 1]
    assert payload["usage"]["prompt_tokens"] == 2


async def test_healthz_reports_states_and_stays_ok(client, manager, sizes):
    assert (await client.get("/healthz")).json() == {"status": "ok", "models": {}}
    sizes["a"] = EST
    await client.post("/admin/load", json={"model_id": "a"})
    await wait_state(manager, "a", "ready")
    assert (await client.get("/healthz")).json()["models"] == {"a": "ready"}
