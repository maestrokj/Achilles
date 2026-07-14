"""MCP surface: JSON-RPC over /mcp, key-only auth, kill-switch, scope (API)."""

from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.services import maintenance
from tests.auth.integration.conftest import AuthorizeFn
from tests.auth.integration.conftest import issue_key_only as _issue_key
from tests.factories.knowledge import create_chunk, create_entity, create_source, grant
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

MCP_URL = "/mcp"
TEXT = "quarterly report alpha"
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _rpc(method: str, request_id: int = 1, **params: object) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params:
        body["params"] = params
    return body


INITIALIZE = _rpc(
    "initialize",
    protocolVersion="2025-06-18",
    capabilities={},
    clientInfo={"name": "test", "version": "0"},
)


async def test_anonymous_and_jwt_are_401(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    resp = await client.post(MCP_URL, json=INITIALIZE, headers=HEADERS)
    assert resp.status_code == 401

    user = await create_user(db_session)
    await authorize(user.email)  # puts a JWT on the client defaults
    resp = await client.post(MCP_URL, json=INITIALIZE, headers=HEADERS)
    assert resp.status_code == 401, "keys only — JWT does not cross the surface"


async def test_initialize_and_the_single_tool(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    raw_key = await _issue_key(client, db_session, authorize)
    headers = {**HEADERS, "Authorization": f"Bearer {raw_key}"}

    init = await client.post(MCP_URL, json=INITIALIZE, headers=headers)
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "achilles"

    tools = await client.post(MCP_URL, json=_rpc("tools/list", 2), headers=headers)
    names = [tool["name"] for tool in tools.json()["result"]["tools"]]
    assert names == ["search_knowledge"]


async def test_tool_call_returns_findings_under_scope(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    source_a = await create_source(db_session)
    source_b = await create_source(db_session)
    entity_a = await create_entity(db_session, source_id=source_a.id, title="Alpha page")
    entity_b = await create_entity(db_session, source_id=source_b.id, title="Beta page")
    for entity in (entity_a, entity_b):
        await grant(db_session, entity_id=entity.id)
        await create_chunk(db_session, entity_id=entity.id, text=TEXT)
    raw_key = await _issue_key(client, db_session, authorize, sources=[source_a.id])
    headers = {**HEADERS, "Authorization": f"Bearer {raw_key}"}

    call = await client.post(
        MCP_URL,
        json=_rpc("tools/call", 3, name="search_knowledge", arguments={"query": TEXT}),
        headers=headers,
    )
    assert call.status_code == 200
    result = call.json()["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["degraded"] is True  # no embedder in the test DB
    assert [item["title"] for item in structured["results"]] == ["Alpha page"]


async def test_kill_switch_answers_403_for_everyone(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    raw_key = await _issue_key(client, db_session, authorize)
    await db_session.execute(sa.text("UPDATE platform_settings SET mcp_enabled = false"))
    await db_session.commit()

    resp = await client.post(
        MCP_URL, json=INITIALIZE, headers={**HEADERS, "Authorization": f"Bearer {raw_key}"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "MCP_DISABLED"


async def test_search_answers_503_during_a_restore(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, redis_durable: Redis
):
    # A restore overwrites the whole store; MCP search must 503 like the Public
    # API and retrieval routes, not run against a half-restored DB.
    raw_key = await _issue_key(client, db_session, authorize)
    await maintenance.enter_maintenance(redis_durable)
    try:
        resp = await client.post(
            MCP_URL,
            json=_rpc("tools/call", 3, name="search_knowledge", arguments={"query": TEXT}),
            headers={**HEADERS, "Authorization": f"Bearer {raw_key}"},
        )
    finally:
        await maintenance.exit_maintenance(redis_durable)
    assert resp.status_code == 503
    assert resp.json()["code"] == "MAINTENANCE"
