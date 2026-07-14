"""Users export — CSV/JSON download of the filtered list (users.html, legend 1)."""

import csv
import io
import json

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p2]

EXPORT_URL = "/api/v1/admin/users/export"


async def test_csv_export_lists_users(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin", full_name="Zoe Admin")
    await create_user(db_session, email="anna@example.com", full_name="Anna Member")
    await authorize(admin.email)

    resp = await client.get(EXPORT_URL)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="users.csv"' in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["id", "email", "full_name", "role", "status", "last_login_at", "created_at"]
    emails = {row[1] for row in rows[1:]}
    assert emails == {admin.email, "anna@example.com"}


async def test_json_export_shape(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    resp = await client.get(EXPORT_URL, params={"format": "json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert 'filename="users.json"' in resp.headers["content-disposition"]

    payload = json.loads(resp.text)
    assert isinstance(payload, list)
    assert payload[0].keys() == {
        "id",
        "email",
        "full_name",
        "role",
        "status",
        "last_login_at",
        "created_at",
    }


async def test_export_respects_role_facet(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await create_user(db_session, email="member@example.com", role="member")
    await authorize(admin.email)

    resp = await client.get(EXPORT_URL, params={"role": "admin"})
    rows = list(csv.reader(io.StringIO(resp.text)))
    emails = {row[1] for row in rows[1:]}
    assert emails == {admin.email}


async def test_export_is_audited(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    await client.get(EXPORT_URL, params={"format": "json"})

    entry = await db_session.scalar(sa.select(AuditLog).where(AuditLog.action == "user.export"))
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.meta == {"format": "json", "count": 1}


async def test_export_forbidden_for_member(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session, role="member")
    await authorize(member.email)

    resp = await client.get(EXPORT_URL)
    assert resp.status_code == 403
