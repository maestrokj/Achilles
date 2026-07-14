"""API keys: machine access — tests.html (P1)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import ApiKey, AuditLog
from tests.auth.integration.conftest import KEYS_URL, AuthorizeFn, issue_key
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_key_shown_once_only_hash_stored(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    raw_key = created["key"]
    assert isinstance(raw_key, str) and raw_key.startswith("ach_")
    assert created["prefix"] == raw_key[:8]

    row = await db_session.scalar(sa.select(ApiKey))
    assert row is not None
    assert raw_key not in (row.key_hash, row.prefix), "raw key never touches the DB"

    listed = (await client.get(KEYS_URL)).json()
    assert "key" not in listed["items"][0]


async def test_key_authenticates_reads(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    raw_key = (await issue_key(client))["key"]

    client.headers["Authorization"] = f"Bearer {raw_key}"
    resp = await client.get(KEYS_URL)
    assert resp.status_code == 200
    assert "X-RateLimit-Remaining" in resp.headers


async def test_key_is_read_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    raw_key = (await issue_key(client))["key"]

    client.headers["Authorization"] = f"Bearer {raw_key}"
    resp = await client.post(KEYS_URL, json={})
    assert resp.status_code == 403, "writes are Agent Engine territory"


async def test_admin_issues_for_member(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await authorize(admin.email)
    created = await issue_key(client, user_id=member.id)
    assert created["user_id"] == member.id


async def test_member_cannot_issue_for_others(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    other = await create_user(db_session)
    await authorize(member.email)
    resp = await client.post(KEYS_URL, json={"user_id": other.id})
    assert resp.status_code == 403


async def test_bad_lifetime_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.post(KEYS_URL, json={"expires_in_days": 7})
    assert resp.status_code == 422


async def test_key_carries_optional_name(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    named = await issue_key(client, name="CI server")
    assert named["name"] == "CI server"
    # Blank collapses to None so the list falls back to the prefix.
    blank = await issue_key(client, name="   ")
    assert blank["name"] is None
    unnamed = await issue_key(client)
    assert unnamed["name"] is None

    listed = (await client.get(KEYS_URL)).json()["items"]
    assert {row["id"]: row["name"] for row in listed}[named["id"]] == "CI server"


async def test_owner_renames_own_key(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    assert created["name"] is None

    resp = await client.patch(f"{KEYS_URL}/{created['id']}", json={"name": "My laptop"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "My laptop"
    # Clearing the name reverts the list to the prefix display.
    cleared = await client.patch(f"{KEYS_URL}/{created['id']}", json={"name": ""})
    assert cleared.json()["name"] is None


async def test_rename_others_key_is_forbidden(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    other = await create_user(db_session)
    await authorize(other.email)
    other_key = await issue_key(client)

    await authorize(member.email)
    resp = await client.patch(f"{KEYS_URL}/{other_key['id']}", json={"name": "hijack"})
    assert resp.status_code == 403


async def test_admin_issues_named_key_owner_can_rename(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await authorize(admin.email)
    created = await issue_key(client, user_id=member.id, name="Data export bot")
    assert created["name"] == "Data export bot"

    # The name belongs to the owner: the member is free to rewrite it.
    await authorize(member.email)
    resp = await client.patch(f"{KEYS_URL}/{created['id']}", json={"name": "my export"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "my export"


async def test_audit_on_rename(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    await client.patch(f"{KEYS_URL}/{created['id']}", json={"name": "renamed"})

    action = await db_session.scalar(
        sa.select(AuditLog.action).where(AuditLog.action == "api_key.rename")
    )
    assert action == "api_key.rename"


async def test_expired_key_is_401(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client, expires_in_days=30)

    await db_session.execute(
        sa.update(ApiKey)
        .where(ApiKey.id == created["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    client.headers["Authorization"] = f"Bearer {created['key']}"
    resp = await client.get(KEYS_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_revoke_is_instant(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    assert (await client.delete(f"{KEYS_URL}/{created['id']}")).status_code == 204

    client.headers["Authorization"] = f"Bearer {created['key']}"
    assert (await client.get(KEYS_URL)).status_code == 401


async def test_revoked_key_stays_listed_and_keeps_its_first_revoked_at(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """The profile shows revoked keys as a record — so the row and its timestamp survive."""
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    assert created["revoked_at"] is None
    assert (await client.delete(f"{KEYS_URL}/{created['id']}")).status_code == 204

    listed = (await client.get(KEYS_URL)).json()["items"]
    assert [(k["id"], k["is_revoked"]) for k in listed] == [(created["id"], True)]
    revoked_at = listed[0]["revoked_at"]
    assert revoked_at is not None

    # Revoking twice must not rewrite the moment of the first revocation.
    assert (await client.delete(f"{KEYS_URL}/{created['id']}")).status_code == 204
    assert (await client.get(KEYS_URL)).json()["items"][0]["revoked_at"] == revoked_at


async def test_deactivation_stamps_revoked_at(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    await authorize(member.email)
    await issue_key(client)

    await authorize(owner.email)
    assert (
        await client.patch(f"/api/v1/admin/users/{member.id}", json={"status": "deactivated"})
    ).status_code == 200

    row = await db_session.scalar(sa.select(ApiKey).where(ApiKey.user_id == member.id))
    assert row is not None
    assert row.is_revoked and row.revoked_at is not None


async def test_deactivation_revokes_keys(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    await authorize(member.email)
    raw_key = (await issue_key(client))["key"]

    await authorize(owner.email)
    resp = await client.patch(f"/api/v1/admin/users/{member.id}", json={"status": "deactivated"})
    assert resp.status_code == 200

    client.headers["Authorization"] = f"Bearer {raw_key}"
    assert (await client.get(KEYS_URL)).status_code == 401


async def test_admin_manages_member_keys_but_not_owner(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)

    await authorize(member.email)
    member_key = await issue_key(client)
    await authorize(owner.email)
    owner_key = await issue_key(client)

    await authorize(admin.email)
    # In scope — Admin manages members.
    assert (await client.get(f"{KEYS_URL}?user_id={member.id}")).status_code == 200
    assert (await client.delete(f"{KEYS_URL}/{member_key['id']}")).status_code == 204
    # Out of scope — an Owner's key is beyond "Admin manages members only".
    assert (await client.get(f"{KEYS_URL}?user_id={owner.id}")).status_code == 403
    assert (await client.delete(f"{KEYS_URL}/{owner_key['id']}")).status_code == 403


async def test_key_rate_limit_60_rpm(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    raw_key = (await issue_key(client))["key"]

    client.headers["Authorization"] = f"Bearer {raw_key}"
    responses = [await client.get(KEYS_URL) for _ in range(61)]
    assert responses[59].status_code == 200
    refused = responses[60]
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]


async def test_audit_on_create_and_revoke(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    created = await issue_key(client)
    await client.delete(f"{KEYS_URL}/{created['id']}")

    actions = (
        await db_session.scalars(
            sa.select(AuditLog.action).where(AuditLog.action.like("api_key.%"))
        )
    ).all()
    assert sorted(actions) == ["api_key.create", "api_key.revoke"]


ADMIN_KEYS_URL = "/api/v1/admin/api-keys"


async def test_company_list_shows_owners_and_facets(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session, full_name="Key Holder")
    await authorize(member.email)
    await client.post(KEYS_URL, json={"name": "nightly backup"})

    await authorize(owner.email)
    created = (await client.post(KEYS_URL, json={"user_id": member.id})).json()
    await client.delete(f"{KEYS_URL}/{created['id']}")

    body = (await client.get(ADMIN_KEYS_URL)).json()
    assert body["total"] == 2
    assert {item["owner"]["email"] for item in body["items"]} == {member.email}

    by_id = {item["id"]: item["status"] for item in body["items"]}
    assert by_id[created["id"]] == "revoked"
    assert sorted(by_id.values()) == ["active", "revoked"]

    revoked = (await client.get(ADMIN_KEYS_URL, params={"status": "revoked"})).json()
    assert [item["id"] for item in revoked["items"]] == [created["id"]]
    active = (await client.get(ADMIN_KEYS_URL, params={"status": "active"})).json()
    assert active["total"] == 1
    assert active["items"][0]["status"] == "active"

    searched = (await client.get(ADMIN_KEYS_URL, params={"q": "Key Holder"})).json()
    assert searched["total"] == 2

    # Search matches the key name too, not just prefix and owner.
    by_name = (await client.get(ADMIN_KEYS_URL, params={"q": "nightly"})).json()
    assert [item["name"] for item in by_name["items"]] == ["nightly backup"]


async def test_company_list_is_admin_gated(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(ADMIN_KEYS_URL)).status_code == 403
