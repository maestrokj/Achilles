"""Audit journal: entries for auth events, append-only at the DB level — tests.html."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog
from tests.auth.integration.conftest import AuthorizeFn, LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p2]

AUDIT_URL = "/api/v1/admin/audit-log"


async def _entries(db_session: AsyncSession, action: str) -> list[AuditLog]:
    result = await db_session.scalars(sa.select(AuditLog).where(AuditLog.action == action))
    return list(result)


async def test_login_success_recorded(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)
    entries = await _entries(db_session, "auth.login")
    assert len(entries) == 1
    assert entries[0].result == "success"
    assert entries[0].actor_id == user.id


async def test_login_failure_recorded(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email, "wrong-password-123")
    entries = await _entries(db_session, "auth.login")
    assert len(entries) == 1
    assert entries[0].result == "failure"
    assert entries[0].actor_id is None


async def test_logout_recorded(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    user = await create_user(db_session)
    await login(user.email)
    await client.post("/api/v1/auth/logout")
    entries = await _entries(db_session, "auth.logout")
    assert len(entries) == 1
    assert entries[0].actor_id == user.id


async def test_setup_recorded(client: AsyncClient, db_session: AsyncSession):
    await client.post(
        "/api/v1/auth/setup",
        json={
            "email": "owner@example.com",
            "full_name": "Owner",
            "password": "correct-horse-battery-staple-2026",
        },
    )
    assert len(await _entries(db_session, "auth.setup")) == 1


async def test_read_is_owner_only(client: AsyncClient, db_session: AsyncSession, login: LoginFn):
    owner = await create_user(db_session, role="owner")
    admin = await create_user(db_session, role="admin")

    token = (await login(owner.email)).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    body = (await client.get("/api/v1/admin/audit-log")).json()
    assert {e["action"] for e in body["items"]} >= {"auth.login"}

    token = (await login(admin.email)).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    assert (await client.get("/api/v1/admin/audit-log")).status_code == 403


async def test_read_exposes_meta_and_user_agent(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    owner = await create_user(db_session, role="owner")
    token = (await login(owner.email)).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    await client.post("/api/v1/auth/logout-all")  # audited with meta={"sessions": N}

    items = (await client.get(AUDIT_URL)).json()["items"]
    by_action = {e["action"]: e for e in items}
    assert by_action["auth.login"]["user_agent"], "httpx always sends a User-Agent"
    assert by_action["auth.logout_all"]["meta"] == {"sessions": 1}
    assert all("meta" in e and "user_agent" in e for e in items)


async def test_entries_survive_actor_deletion(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    await login(member.email)  # leaves an auth.login entry by the member

    token = (await login(owner.email)).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    assert (await client.delete(f"/api/v1/admin/users/{member.id}")).status_code == 204

    survivors = (
        await db_session.scalars(sa.select(AuditLog).where(AuditLog.actor_id == member.id))
    ).all()
    assert survivors, "no FK on actor_id: entries outlive the account"

    # The email is snapshotted at write time, so the API still names a deleted
    # actor instead of falling back to a bare numeric id.
    by_member = (await client.get(AUDIT_URL, params={"actor_id": member.id})).json()
    assert by_member["items"], "the member's own entries are still readable"
    assert all(item["actor_email"] == member.email for item in by_member["items"]), (
        "the deleted actor still reads by email — the snapshot outlives the account"
    )


async def test_update_is_rejected_by_db(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.update(AuditLog).values(result="failure"))
    await db_session.rollback()


async def test_delete_is_rejected_by_db(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn
):
    user = await create_user(db_session)
    await login(user.email)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(sa.delete(AuditLog))
    await db_session.rollback()


async def test_audit_facets_and_search(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    await login(member.email)  # auth.login row for the member
    await authorize(owner.email)
    await client.patch(f"/api/v1/admin/users/{member.id}", json={"full_name": "Renamed"})

    # An admin renaming someone else's account leaves a trace of its own.
    renames = await _entries(db_session, "user.profile_update")
    assert len(renames) == 1
    assert renames[0].actor_id == owner.id
    assert renames[0].target_id == str(member.id)

    everything = (await client.get(AUDIT_URL)).json()
    assert everything["total"] >= 2
    assert "auth" in everything["groups"], "the group catalogue rides along with the page"

    auth_only = (await client.get(AUDIT_URL, params={"action_group": "auth"})).json()
    assert auth_only["items"], "logins land in the auth group"
    assert all(item["action"].startswith(("auth.", "password.")) for item in auth_only["items"])

    by_actor = (await client.get(AUDIT_URL, params={"actor_id": owner.id})).json()
    assert all(item["actor_id"] == owner.id for item in by_actor["items"])
    assert all(item["actor_email"] == owner.email for item in by_actor["items"]), (
        "the journal resolves the actor to an email — the wireframe column is a user link"
    )

    unknown_group = await client.get(AUDIT_URL, params={"action_group": "nonsense"})
    assert unknown_group.status_code == 422


async def test_audit_free_text_search(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    """The `q` box (audit-log.html, "Search by object, actor or IP") hits actor
    email and the target id — the two forensics handles the wireframe exposes."""
    owner = await create_user(db_session, role="owner", email="owner@audit.example")
    member = await create_user(db_session, email="member@audit.example")
    await login(member.email)  # auth.login by the member (no target)
    await authorize(owner.email)
    # An owner action that carries target_id == member.id and actor_email == owner.
    await client.patch(f"/api/v1/admin/users/{member.id}", json={"full_name": "Renamed"})

    by_actor_email = (await client.get(AUDIT_URL, params={"q": owner.email})).json()
    assert by_actor_email["items"], "the actor's email is a searchable handle"
    assert all(item["actor_email"] == owner.email for item in by_actor_email["items"])

    by_target = (await client.get(AUDIT_URL, params={"q": str(member.id)})).json()
    assert any(
        item["target_id"] == str(member.id) and item["action"] == "user.profile_update"
        for item in by_target["items"]
    ), "the object id locates the row that acted on it"

    miss = (await client.get(AUDIT_URL, params={"q": "no-such-needle-zzz"})).json()
    assert miss["items"] == [] and miss["total"] == 0
