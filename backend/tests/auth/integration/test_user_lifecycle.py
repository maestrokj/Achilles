"""Admin user management lifecycle — tests.html (P1)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import RefreshToken
from tests.auth.integration.conftest import AuthorizeFn, LoginFn, Outbox
from tests.factories.users import DEFAULT_PASSWORD, create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

USERS_URL = "/api/v1/admin/users"


async def test_owner_deactivates_admin(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    target = await create_user(db_session, role="admin")
    await authorize(owner.email)

    resp = await client.patch(f"{USERS_URL}/{target.id}", json={"status": "deactivated"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"


async def test_admin_cannot_touch_admins(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    other_admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    resp = await client.patch(f"{USERS_URL}/{other_admin.id}", json={"status": "deactivated"})
    assert resp.status_code == 403


async def test_admin_manages_member(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await authorize(admin.email)

    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"status": "deactivated"})
    assert resp.status_code == 200


async def test_self_deactivation_is_403(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    resp = await client.patch(f"{USERS_URL}/{owner.id}", json={"status": "deactivated"})
    assert resp.status_code == 403


async def test_deactivation_kills_sessions_and_blocks_login(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    await login(member.email)
    await authorize(owner.email)

    await client.patch(f"{USERS_URL}/{member.id}", json={"status": "deactivated"})
    remaining = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == member.id)
    )
    assert remaining == 0
    client.headers.pop("Authorization", None)
    assert (await login(member.email)).status_code == 401


async def test_reactivation_restores_login(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member", status="deactivated")
    await authorize(owner.email)

    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"status": "active"})
    assert resp.status_code == 200
    client.headers.pop("Authorization", None)
    assert (await login(member.email)).status_code == 200


async def test_role_change_is_owner_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await authorize(admin.email)
    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"role": "admin"})
    assert resp.status_code == 403


async def test_owner_promotes_member(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    await authorize(owner.email)
    resp = await client.patch(f"{USERS_URL}/{member.id}", json={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


async def test_admin_terminates_member_sessions(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await login(member.email)
    await authorize(admin.email)

    resp = await client.post(f"{USERS_URL}/{member.id}/terminate-sessions")
    assert resp.status_code == 204
    remaining = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == member.id)
    )
    assert remaining == 0


async def test_admin_reset_issues_temp_password(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await authorize(admin.email)

    resp = await client.post(f"{USERS_URL}/{member.id}/reset-password")
    assert resp.status_code == 200
    # The body carries a live credential — it must never be cached or leak a referrer.
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.json()["mode"] == "temp_password", "no SMTP → the fallback path"
    temp_password = resp.json()["temp_password"]

    client.headers.pop("Authorization", None)
    assert (await login(member.email, DEFAULT_PASSWORD)).status_code == 401
    body = (await login(member.email, temp_password)).json()
    assert body["must_change_password"] is True


async def test_admin_reset_with_smtp_sends_a_link(
    client: AsyncClient,
    db_session: AsyncSession,
    login: LoginFn,
    authorize: AuthorizeFn,
    outbox: Outbox,
):
    """SMTP available → the primary path: a reset letter, no temp password."""
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session, role="member")
    await authorize(admin.email)

    resp = await client.post(f"{USERS_URL}/{member.id}/reset-password")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "link", "temp_password": None}

    (letter,) = await outbox.drain()
    assert letter.to == member.email

    client.headers.pop("Authorization", None)
    reset = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": letter.token, "new_password": "fresh-horse-staple-2027"},
    )
    assert reset.status_code == 204
    assert (await login(member.email, "fresh-horse-staple-2027")).status_code == 200


async def test_users_search(client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn):
    owner = await create_user(db_session, role="owner")
    await create_user(db_session, full_name="Alice Wonder", email="alice@example.com")
    await create_user(db_session, full_name="Bob Builder", email="bob@example.com")
    await authorize(owner.email)

    body = (await client.get(USERS_URL, params={"q": "alice"})).json()
    assert [u["full_name"] for u in body["items"]] == ["Alice Wonder"]
    assert body["total"] == 1


async def test_users_list_paginates_with_facets(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    for _ in range(26):
        await create_user(db_session)
    await authorize(owner.email)

    resp = await client.get(USERS_URL, params={"per_page": 25})
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert (len(first["items"]), first["total"], first["page"]) == (25, 27, 1)
    second = (await client.get(USERS_URL, params={"per_page": 25, "page": 2})).json()
    ids = [u["id"] for u in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 27

    # A page past the end clamps to the last one instead of answering empty.
    clamped = (await client.get(USERS_URL, params={"per_page": 25, "page": 99})).json()
    assert clamped["page"] == 2

    members = (await client.get(USERS_URL, params={"role": "member"})).json()
    assert members["total"] == 26
