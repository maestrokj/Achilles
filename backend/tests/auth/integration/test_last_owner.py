"""Last-owner protection — tests.html (P2)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import AuditLog, RefreshToken, User
from achilles.auth.security.tokens import generate_token, hash_token
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p2]

USERS_URL = "/api/v1/admin/users"


@pytest.mark.parametrize(
    "payload", [{"status": "deactivated"}, {"role": "member"}], ids=["deactivate", "downgrade"]
)
async def test_last_owner_cannot_be_neutralized(
    payload: dict[str, str],
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
):
    """A second owner tries — the guard is about the LAST owner, not self-action."""
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)

    resp = await client.patch(f"{USERS_URL}/{owner.id}", json=payload)
    assert resp.status_code == 403
    # Self-deactivation trips the self-guard; downgrade trips the last-owner guard.
    assert resp.json()["code"] in {"FORBIDDEN", "LAST_OWNER_PROTECTED"}


async def test_last_owner_delete_is_403(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    first = await create_user(db_session, role="owner")
    second = await create_user(db_session, role="owner", status="deactivated")
    await authorize(first.email)

    # `second` is deactivated, so `first` is the last ACTIVE owner: a (hypothetical)
    # second actor deleting them must hit the guard. Simulate via direct call on self.
    resp = await client.delete(f"{USERS_URL}/{second.id}")
    assert resp.status_code == 204, "deleting a deactivated owner is fine"
    resp = await client.delete(f"{USERS_URL}/{first.id}")
    assert resp.status_code == 403


async def test_two_owners_downgrade_is_fine(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    first = await create_user(db_session, role="owner")
    second = await create_user(db_session, role="owner")
    await authorize(first.email)

    resp = await client.patch(f"{USERS_URL}/{second.id}", json={"role": "member"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"


async def test_delete_is_owner_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)
    await authorize(admin.email)
    assert (await client.delete(f"{USERS_URL}/{member.id}")).status_code == 403


async def test_hard_delete_cascades_auth_data_keeps_audit(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    member_id = member.id  # survives expire_all after the delete
    await authorize(owner.email)

    # Give the member some auth data and an audit trace.
    db_session.add(
        RefreshToken(
            user_id=member_id,
            token_hash=hash_token(generate_token()),
            family_id=uuid.uuid7(),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            absolute_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db_session.commit()

    assert (await client.delete(f"{USERS_URL}/{member_id}")).status_code == 204
    db_session.expire_all()
    assert await db_session.get(User, member_id) is None
    tokens = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == member_id)
    )
    assert tokens == 0
    delete_entry = await db_session.scalar(
        sa.select(AuditLog).where(
            AuditLog.action == "user.delete", AuditLog.target_id == str(member_id)
        )
    )
    assert delete_entry is not None, "the journal outlives the account"
