"""Role matrix for the Settings zone: read Owner+Admin, write Owner only."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/settings"


async def test_owner_reads_and_writes(client: AsyncClient, as_owner: None):
    assert (await client.get(URL)).status_code == 200
    assert (await client.patch(URL, json={"org_name": "Acme"})).status_code == 200


async def test_admin_reads_but_cannot_write(client: AsyncClient, as_admin: None):
    assert (await client.get(URL)).status_code == 200
    resp = await client.patch(URL, json={"org_name": "Acme"})
    assert resp.status_code == 403


async def test_member_gets_403(client: AsyncClient, as_member: None):
    assert (await client.get(URL)).status_code == 403
    assert (await client.patch(URL, json={"org_name": "Acme"})).status_code == 403


async def test_anonymous_gets_401(client: AsyncClient):
    assert (await client.get(URL)).status_code == 401
