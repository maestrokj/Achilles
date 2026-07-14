"""Admin email change — tests.html (P1)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog, RefreshToken
from tests.auth.integration.conftest import AuthorizeFn, LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

USERS_URL = "/api/v1/admin/users"


async def test_admin_changes_member_email(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await login(member.email)  # an active session that must die on email change
    await authorize(admin.email)

    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"email": "New.Mail@Example.Com"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "new.mail@example.com", "stored lower-cased"

    remaining = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == member.id)
    )
    assert remaining == 0

    client.headers.pop("Authorization", None)
    assert (await login("new.mail@example.com")).status_code == 200


async def test_taken_email_is_409(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    other = await create_user(db_session, email="taken@example.com")
    await authorize(admin.email)

    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"email": "Taken@Example.Com"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
    assert other.email == "taken@example.com"


async def test_email_change_audited(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await authorize(admin.email)
    await client.patch(f"{USERS_URL}/{member.id}", json={"email": "fresh@example.com"})

    entry = await db_session.scalar(
        sa.select(AuditLog).where(AuditLog.action == "user.email_change")
    )
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.target_id == str(member.id)
