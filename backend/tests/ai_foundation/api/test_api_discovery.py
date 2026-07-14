"""Discovery + connectivity probe: dialects, upstream failure is a 502 (tests.html, P1)."""

import pytest
import respx
from httpx import AsyncClient, ConnectError, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.ai import create_provider

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


@respx.mock(assert_all_mocked=False)
async def test_openai_dialect(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(db_session, adapter="openai")
    catalog = respx_mock.get("https://api.openai.com/v1/models").mock(
        return_value=Response(
            200, json={"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3-large"}]}
        )
    )

    resp = await client.get(f"{BASE}/providers/{provider.id}/discovery")
    assert resp.status_code == 200
    # No type in an OpenAI catalog — inferred from the id: "embed" → embedding.
    assert {m["model_id"]: m["model_type"] for m in resp.json()["models"]} == {
        "gpt-4o": "chat",
        "text-embedding-3-large": "embedding",
    }
    assert catalog.called


@respx.mock(assert_all_mocked=False)
async def test_ollama_dialect_uses_base_url(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(
        db_session, adapter="ollama", kind="local", base_url="http://ollama:11434"
    )
    respx_mock.get("http://ollama:11434/api/tags").mock(
        return_value=Response(
            200, json={"models": [{"name": "llama3.2"}, {"name": "nomic-embed-text"}]}
        )
    )

    resp = await client.get(f"{BASE}/providers/{provider.id}/discovery")
    assert resp.status_code == 200
    assert resp.json()["models"] == [
        {"model_id": "llama3.2", "display_name": None, "model_type": "chat"},
        {"model_id": "nomic-embed-text", "display_name": None, "model_type": "embedding"},
    ]


@respx.mock(assert_all_mocked=False)
async def test_google_dialect_reads_supported_methods(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(db_session, adapter="google", kind="cloud")
    respx_mock.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "displayName": "Gemini 2.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "displayName": "Text Embedding 004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )
    )

    resp = await client.get(f"{BASE}/providers/{provider.id}/discovery")
    assert resp.status_code == 200
    # Gemini's catalog is authoritative: embedContent → embedding, not a name guess.
    assert {m["model_id"]: m["model_type"] for m in resp.json()["models"]} == {
        "gemini-2.5-flash": "chat",
        "text-embedding-004": "embedding",
    }


@respx.mock(assert_all_mocked=False)
async def test_unreachable_upstream_is_502(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(db_session, adapter="openai")
    respx_mock.get("https://api.openai.com/v1/models").mock(side_effect=ConnectError("boom"))

    resp = await client.get(f"{BASE}/providers/{provider.id}/discovery")
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_UNREACHABLE"


@respx.mock(assert_all_mocked=False)
async def test_upstream_401_is_502_with_reason(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(db_session, adapter="openai")
    respx_mock.get("https://api.openai.com/v1/models").mock(return_value=Response(401))

    resp = await client.get(f"{BASE}/providers/{provider.id}/discovery")
    assert resp.status_code == 502
    assert "401" in resp.json()["detail"]


@respx.mock(assert_all_mocked=False)
async def test_check_config_probes_a_draft_without_persisting(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    as_admin: None,
):
    route = respx_mock.get("https://api.openai.com/v1/models")

    route.mock(return_value=Response(200, json={"data": []}))
    ok = await client.post(
        f"{BASE}/providers/check-config",
        json={"adapter": "openai", "api_key": "sk-draft-1234abcd"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "active"
    assert ok.json()["last_check_at"] is not None

    route.mock(side_effect=ConnectError("down"))
    failed = await client.post(f"{BASE}/providers/check-config", json={"adapter": "openai"})
    assert failed.status_code == 200
    assert failed.json()["status"] == "error"

    listed = await client.get(f"{BASE}/providers")
    assert {p["name"] for p in listed.json()} == {"Platform"}, "the draft never touched the table"


@respx.mock(assert_all_mocked=False)
async def test_check_config_sends_the_draft_key(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    as_admin: None,
):
    route = respx_mock.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": "Bearer sk-draft-1234abcd"},
    ).mock(return_value=Response(200, json={"data": []}))

    resp = await client.post(
        f"{BASE}/providers/check-config",
        json={"adapter": "openai", "api_key": "sk-draft-1234abcd"},
    )
    assert resp.json()["status"] == "active"
    assert route.called


@respx.mock(assert_all_mocked=False)
async def test_check_writes_status(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    provider = await create_provider(db_session, adapter="openai")
    route = respx_mock.get("https://api.openai.com/v1/models")

    route.mock(return_value=Response(200, json={"data": []}))
    ok = await client.post(f"{BASE}/providers/{provider.id}/check")
    assert ok.status_code == 200
    assert ok.json()["status"] == "active"

    route.mock(side_effect=ConnectError("down"))
    failed = await client.post(f"{BASE}/providers/{provider.id}/check")
    assert failed.status_code == 200
    assert failed.json()["status"] == "error"

    listed = await client.get(f"{BASE}/providers/{provider.id}")
    assert listed.json()["status"] == "error"
    assert listed.json()["last_check_at"] is not None
