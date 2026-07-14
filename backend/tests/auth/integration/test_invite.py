"""Single invite: create + accept — tests.html (P1)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import User
from tests.auth.integration.conftest import AuthorizeFn, Outbox, set_smtp
from tests.factories.users import DEFAULT_PASSWORD as STRONG_PASSWORD
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

INVITES_URL = "/api/v1/invites"


def _accept_url(token: str) -> str:
    return f"{INVITES_URL}/{token}/accept"


async def _accept(client: AsyncClient, token: str, **overrides: str):
    payload = {"full_name": "New Person", "password": STRONG_PASSWORD} | overrides
    return await client.post(_accept_url(token), json=payload)


async def test_invite_and_accept_full_flow(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    resp = await client.post(INVITES_URL, json={"email": "Fresh@Example.Com", "role": "member"})
    assert resp.status_code == 201
    assert resp.json()["email"] == "fresh@example.com"
    (to, token, role) = outbox.invites[0]
    assert (to, role) == ("fresh@example.com", "member")

    client.headers.pop("Authorization")
    accepted = await _accept(client, token)
    assert accepted.status_code == 201
    body = accepted.json()
    assert body["user"]["role"] == "member"
    assert body["access_token"], "accept logs the person straight in"

    user = await db_session.scalar(sa.select(User).where(User.email == "fresh@example.com"))
    assert user is not None


async def test_admin_cannot_grant_admin(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    resp = await client.post(INVITES_URL, json={"email": "x@example.com", "role": "admin"})
    assert resp.status_code == 403


async def test_owner_grants_any_role(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    resp = await client.post(INVITES_URL, json={"email": "x@example.com", "role": "owner"})
    assert resp.status_code == 201


async def test_smtp_unconfigured_is_409(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """No outbox override → the default stub sender → invites are refused."""
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    resp = await client.post(INVITES_URL, json={"email": "x@example.com"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "SMTP_NOT_CONFIGURED"


async def test_existing_email_is_409_case_insensitive(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await create_user(db_session, email="taken@example.com")
    await authorize(owner.email)
    resp = await client.post(INVITES_URL, json={"email": "Taken@Example.Com"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMAIL_TAKEN"


async def test_expired_invite_is_410(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    with time_machine.travel(datetime(2026, 7, 2, 12, 0, tzinfo=UTC), tick=False) as traveller:
        await authorize(owner.email)
        created = await client.post(INVITES_URL, json={"email": "slow@example.com"})
        assert created.status_code == 201, created.text
        (_, token, _) = outbox.invites[0]
        traveller.shift(timedelta(hours=49))
        client.headers.pop("Authorization")
        resp = await _accept(client, token)
    assert resp.status_code == 410
    assert resp.json()["code"] == "INVITE_EXPIRED"


async def test_used_invite_is_410(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    await client.post(INVITES_URL, json={"email": "once@example.com"})
    (_, token, _) = outbox.invites[0]

    client.headers.pop("Authorization")
    assert (await _accept(client, token)).status_code == 201
    reuse = await _accept(client, token)
    assert reuse.status_code == 410
    assert reuse.json()["code"] == "INVITE_USED"


async def test_reinvite_kills_previous_link(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    await client.post(INVITES_URL, json={"email": "again@example.com"})
    await client.post(INVITES_URL, json={"email": "again@example.com"})
    (_, first_token, _) = outbox.invites[0]
    (_, second_token, _) = outbox.invites[1]

    client.headers.pop("Authorization")
    assert (await _accept(client, first_token)).status_code == 410
    assert (await _accept(client, second_token)).status_code == 201


async def test_weak_password_on_accept_is_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    await client.post(INVITES_URL, json={"email": "weak@example.com"})
    (_, token, _) = outbox.invites[0]

    client.headers.pop("Authorization")
    resp = await _accept(client, token, password="password123")
    assert resp.status_code == 422


async def test_invites_list_with_status_facets(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    await client.post(INVITES_URL, json={"email": "stale@example.com"})
    del outbox

    with time_machine.travel(datetime.now(UTC) + timedelta(hours=50), tick=False):
        await authorize(owner.email)  # the pre-travel access token has expired
        await client.post(INVITES_URL, json={"email": "fresh@example.com"})

        assert (await client.get(INVITES_URL)).json()["total"] == 2
        expired = (await client.get(INVITES_URL, params={"status": "expired"})).json()
        assert [i["email"] for i in expired["items"]] == ["stale@example.com"]
        assert expired["items"][0]["status"] == "expired"
        pending = (await client.get(INVITES_URL, params={"status": "pending"})).json()
        assert [i["email"] for i in pending["items"]] == ["fresh@example.com"]
        searched = (await client.get(INVITES_URL, params={"q": "fresh"})).json()
        assert searched["total"] == 1


async def test_resend_rotates_the_link(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    created = (await client.post(INVITES_URL, json={"email": "rotate@example.com"})).json()
    (_, old_token, _) = outbox.invites[0]

    resent = await client.post(f"{INVITES_URL}/{created['id']}/resend")
    assert resent.status_code == 201
    (_, new_token, _) = outbox.invites[1]
    assert new_token != old_token

    client.headers.pop("Authorization")
    assert (await _accept(client, old_token)).status_code == 410
    assert (await _accept(client, new_token)).status_code == 201


async def test_revoke_kills_the_pending_invite(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    created = (await client.post(INVITES_URL, json={"email": "gone@example.com"})).json()
    (_, token, _) = outbox.invites[0]

    assert (await client.delete(f"{INVITES_URL}/{created['id']}")).status_code == 204
    assert (await client.get(INVITES_URL)).json()["total"] == 0

    client.headers.pop("Authorization")
    assert (await _accept(client, token)).status_code == 410


async def test_accepted_invite_cannot_be_resent_or_revoked(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    created = (await client.post(INVITES_URL, json={"email": "done@example.com"})).json()
    (_, token, _) = outbox.invites[0]
    owner_header = client.headers["Authorization"]

    client.headers.pop("Authorization")
    assert (await _accept(client, token)).status_code == 201

    client.headers["Authorization"] = owner_header
    assert (await client.post(f"{INVITES_URL}/{created['id']}/resend")).status_code == 409
    assert (await client.delete(f"{INVITES_URL}/{created['id']}")).status_code == 409


async def test_resend_requires_smtp(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    created = (await client.post(INVITES_URL, json={"email": "dark@example.com"})).json()

    del outbox
    await set_smtp(db_session, enabled=False)
    resp = await client.post(f"{INVITES_URL}/{created['id']}/resend")
    assert resp.status_code == 409
    assert resp.json()["code"] == "SMTP_NOT_CONFIGURED"
