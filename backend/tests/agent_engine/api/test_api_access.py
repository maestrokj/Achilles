"""Access matrix of every Agent Engine route: anonymous and role floors (P0)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p0]

OWNER_ROUTES = (
    ("get", "/api/v1/agents"),
    ("post", "/api/v1/agents"),
    ("get", "/api/v1/agents/options"),
    ("get", "/api/v1/agents/1"),
    ("patch", "/api/v1/agents/1"),
    ("delete", "/api/v1/agents/1"),
    ("post", "/api/v1/agents/1/run"),
    ("get", "/api/v1/agents/1/runs"),
)

ADMIN_ROUTES = (
    ("get", "/api/v1/admin/agents"),
    ("get", "/api/v1/admin/agents/1"),
    ("get", "/api/v1/admin/agents/1/runs"),
    ("patch", "/api/v1/admin/agents/1/pause"),
    ("get", "/api/v1/admin/agent-limits"),
    ("patch", "/api/v1/admin/agent-limits"),
)


@pytest.mark.parametrize(("method", "url"), OWNER_ROUTES + ADMIN_ROUTES)
async def test_anonymous_is_401_everywhere(client: AsyncClient, method: str, url: str) -> None:
    resp = await client.request(method, url, json={})
    assert resp.status_code == 401, (method, url, resp.status_code)


@pytest.mark.parametrize(("method", "url"), ADMIN_ROUTES)
async def test_member_is_403_on_admin_routes(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    method: str,
    url: str,
) -> None:
    member = await create_user(db_session)
    await authorize(member.email)
    resp = await client.request(method, url, json={})
    assert resp.status_code == 403, (method, url, resp.status_code)
