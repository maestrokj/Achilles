"""Tools HTTP contract: merge view, write-only secret, probe (tests.html, P1)."""

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import UserRole
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import get_tool
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai/tools"


async def test_list_merges_registry_and_rows(client: AsyncClient, as_admin: None):
    resp = await client.get(BASE)
    assert resp.status_code == 200
    tools = {t["name"]: t for t in resp.json()}
    assert {"web_search", "fetch_url"} <= set(tools)
    for name in ("web_search", "fetch_url"):
        assert tools[name]["id"] is not None  # seeded rows
        assert tools[name]["chat_enabled"] is False
        assert tools[name]["credential_is_set"] is False
        assert tools[name]["parameters"]["type"] == "object"


async def test_credential_is_write_only(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    row = await get_tool(db_session, "web_search")
    resp = await client.patch(
        f"{BASE}/{row.id}",
        json={"config": {"provider": "tavily"}, "credential": "tv-secret-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["credential_is_set"] is True
    assert "tv-secret-key" not in resp.text  # never round-trips

    await db_session.refresh(row)
    assert row.credential_enc is not None
    assert "tv-secret-key" not in row.credential_enc  # encrypted at rest


async def test_flags_toggle_independently(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    row = await get_tool(db_session, "fetch_url")
    resp = await client.patch(f"{BASE}/{row.id}", json={"chat_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["chat_enabled"] is True
    assert resp.json()["agents_allowed"] is False


async def test_unknown_type_create_is_422(client: AsyncClient, as_admin: None):
    resp = await client.post(BASE, json={"name": "teleport"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "UNKNOWN_TOOL"


async def test_duplicate_instance_is_409(client: AsyncClient, as_admin: None):
    resp = await client.post(BASE, json={"name": "web_search"})  # seeded already
    assert resp.status_code == 409


async def test_delete_resets_to_type_defaults(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    row = await get_tool(db_session, "fetch_url")
    assert (await client.delete(f"{BASE}/{row.id}")).status_code == 204

    listed = await client.get(BASE)
    fetch_url = next(t for t in listed.json() if t["name"] == "fetch_url")
    assert fetch_url["id"] is None  # type still visible, instance gone


@respx.mock(assert_all_mocked=False)
async def test_probe_writes_status(
    respx_mock: respx.MockRouter,
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
):
    row = await get_tool(db_session, "web_search")
    await client.patch(
        f"{BASE}/{row.id}",
        json={"config": {"provider": "brave"}, "credential": "brave-key"},
    )
    respx_mock.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json={"web": {"results": []}})
    )

    resp = await client.post(f"{BASE}/{row.id}/check")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    listed = await client.get(BASE)
    web_search = next(t for t in listed.json() if t["name"] == "web_search")
    assert web_search["status"] == "active"
    assert web_search["last_check_at"] is not None


async def test_member_is_403(client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn):
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)
    assert (await client.get(BASE)).status_code == 403


async def test_anonymous_is_401(client: AsyncClient):
    assert (await client.get(BASE)).status_code == 401
