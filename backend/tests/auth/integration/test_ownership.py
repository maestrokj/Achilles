"""Ownership over RBAC (IDOR guard) — tests.html (P1)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import KEYS_URL, AuthorizeFn, issue_key
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_own_resource_200(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    assert (await client.delete(f"{KEYS_URL}/{created['id']}")).status_code == 204


async def test_foreign_resource_403(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    alice = await create_user(db_session)
    bob = await create_user(db_session)
    await authorize(alice.email)
    created = await issue_key(client)

    await authorize(bob.email)
    resp = await client.delete(f"{KEYS_URL}/{created['id']}")
    assert resp.status_code == 403, "a member must not touch someone else's key (IDOR)"


async def test_foreign_list_403_for_member(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    alice = await create_user(db_session)
    bob = await create_user(db_session)
    await authorize(bob.email)
    resp = await client.get(KEYS_URL, params={"user_id": alice.id})
    assert resp.status_code == 403


async def test_admin_oversight_reaches_foreign_keys(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await authorize(member.email)
    created = await issue_key(client)

    await authorize(admin.email)
    listed = (await client.get(KEYS_URL, params={"user_id": member.id})).json()
    assert [k["id"] for k in listed["items"]] == [created["id"]]
    assert (await client.delete(f"{KEYS_URL}/{created['id']}")).status_code == 204
