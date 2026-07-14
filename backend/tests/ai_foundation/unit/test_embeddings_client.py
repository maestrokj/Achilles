"""embeddings_client: best-effort contract — never raises, only reports (unit).

Uses the conftest's embeddings_runtime_mock router (the autouse egress guard
already owns the http://embeddings host); adding a second respx layer here
would fight it for the interception.
"""

import json

import httpx
import pytest
import respx

from achilles.ai_foundation.services import embeddings_client

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "http://embeddings:80"


async def test_ensure_loaded_calls_runtime(embeddings_runtime_mock: respx.MockRouter):
    assert await embeddings_client.ensure_loaded(BASE, "BAAI/bge-m3") is True
    load_calls = [
        call for call in embeddings_runtime_mock.calls if call.request.url.path == "/admin/load"
    ]
    assert len(load_calls) == 1
    assert json.loads(load_calls[0].request.read()) == {"model_id": "BAAI/bge-m3"}


async def test_unreachable_runtime_is_false_not_raise(
    embeddings_runtime_mock: respx.MockRouter,
):
    embeddings_runtime_mock.post(url__startswith="http://embeddings").mock(
        side_effect=httpx.ConnectError("down")
    )
    assert await embeddings_client.ensure_loaded(BASE, "BAAI/bge-m3") is False


async def test_runtime_status_none_on_failure(embeddings_runtime_mock: respx.MockRouter):
    embeddings_runtime_mock.get(f"{BASE}/admin/status").mock(return_value=httpx.Response(500))
    assert await embeddings_client.runtime_status(BASE) is None


async def test_runtime_status_none_on_stale_image_404(embeddings_runtime_mock: respx.MockRouter):
    """An older runtime without /admin/status degrades to 'unreachable', not a crash."""
    embeddings_runtime_mock.get(f"{BASE}/admin/status").mock(return_value=httpx.Response(404))
    assert await embeddings_client.runtime_status(BASE) is None


async def test_runtime_status_parses_states(embeddings_runtime_mock: respx.MockRouter):
    embeddings_runtime_mock.get(f"{BASE}/admin/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "budget_bytes": 123,
                "desired": "BAAI/bge-m3",
                "models": {"BAAI/bge-m3": {"state": "error", "error": "weights corrupted"}},
            },
        )
    )
    status = await embeddings_client.runtime_status(BASE)
    assert status is not None
    assert status.desired == "BAAI/bge-m3"
    assert status.state_of("BAAI/bge-m3") == "error"
    assert status.error_of("BAAI/bge-m3") == "weights corrupted"
    assert status.state_of("never-loaded") == "not_loaded"


async def test_preflight_parses_fit(embeddings_runtime_mock: respx.MockRouter):
    # Same catch-all pattern as the conftest route — respx merges them, so this
    # .mock() call replaces the default answer instead of shadowing it.
    embeddings_runtime_mock.post(url__startswith="http://embeddings").mock(
        return_value=httpx.Response(
            200, json={"fits": False, "required_bytes": 9, "budget_bytes": 5}
        )
    )
    result = await embeddings_client.preflight(BASE, "BAAI/bge-m3")
    assert result is not None
    assert result.fits is False
    assert (result.required_bytes, result.budget_bytes) == (9, 5)


async def test_preflight_none_on_alien_payload(embeddings_runtime_mock: respx.MockRouter):
    """The conftest default answer has no `fits` — an old image degrades to None."""
    assert await embeddings_client.preflight(BASE, "BAAI/bge-m3") is None
