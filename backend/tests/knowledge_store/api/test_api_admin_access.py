"""Admin knowledge ops are Owner/Admin only; Member → 403, anonymous → 401 (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

from achilles.auth.constants import UserRole

pytestmark = [pytest.mark.api, pytest.mark.p1]

ADMIN_CALLS = [
    ("GET", "/api/v1/admin/knowledge/sources", None),
    ("POST", "/api/v1/admin/knowledge/reindex", None),
    ("POST", "/api/v1/admin/knowledge/backup", None),
    ("POST", "/api/v1/admin/knowledge/restore", {"snapshot_id": 1}),
]


@pytest.mark.parametrize(("method", "url", "body"), ADMIN_CALLS)
async def test_anonymous_is_401(
    client: AsyncClient, method: str, url: str, body: dict[str, object] | None
):
    resp = await client.request(method, url, json=body)
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "url", "body"), ADMIN_CALLS)
async def test_member_is_403(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    method: str,
    url: str,
    body: dict[str, object] | None,
):
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)
    resp = await client.request(method, url, json=body)
    assert resp.status_code == 403


@pytest.mark.parametrize("role", [UserRole.ADMIN.value, UserRole.OWNER.value])
async def test_admin_and_owner_pass_the_gate(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, role: str
):
    user = await create_user(db_session, role=role)
    await authorize(user.email)
    assert (await client.get("/api/v1/admin/knowledge/sources")).status_code == 200
    assert (await client.post("/api/v1/admin/knowledge/reindex")).status_code == 202
