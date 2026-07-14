"""RBAC: permission gate, role matrix, stale-claim semantics — tests.html (P1)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

USERS_URL = "/api/v1/admin/users"
AUDIT_URL = "/api/v1/admin/audit-log"


async def test_no_token_is_401(client: AsyncClient):
    resp = await client.get(USERS_URL)
    assert resp.status_code == 401


async def test_garbage_token_is_401(client: AsyncClient):
    client.headers["Authorization"] = "Bearer not.a.jwt"
    resp = await client.get(USERS_URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_INVALID"


@pytest.mark.parametrize(
    ("role", "users_status", "audit_status"),
    [("member", 403, 403), ("admin", 200, 403), ("owner", 200, 200)],
)
async def test_role_matrix(
    role: str,
    users_status: int,
    audit_status: int,
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
):
    user = await create_user(db_session, role=role)
    await authorize(user.email)
    assert (await client.get(USERS_URL)).status_code == users_status
    assert (await client.get(AUDIT_URL)).status_code == audit_status


async def test_permission_refusal_is_403_forbidden(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session, role="member")
    await authorize(user.email)
    body = (await client.get(USERS_URL)).json()
    assert body["code"] == "FORBIDDEN"


async def test_deactivated_user_refused_on_critical_endpoint(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """Critical checks read role *and* status from the DB (authentication.html:614):
    an admin's still-valid token stops opening the admin zone the instant the
    account is deactivated — no waiting out the 15-min window."""
    user = await create_user(db_session, role="admin")
    await authorize(user.email)
    assert (await client.get(USERS_URL)).status_code == 200

    user.status = "deactivated"
    await db_session.commit()
    resp = await client.get(USERS_URL)
    assert resp.status_code == 403
    assert resp.json()["code"] == "ACCOUNT_DEACTIVATED"


async def test_deactivated_user_rides_out_window_on_ordinary_endpoint(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """The deliberate v1 trade-off: a plain (non-permissioned) endpoint stays open
    for the ≤15-min stateless window after deactivation — refresh is already dead,
    so a new token can't be minted; instant invalidation everywhere is v2."""
    user = await create_user(db_session)
    await authorize(user.email)
    user.status = "deactivated"
    await db_session.commit()

    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_stale_role_claim_reads_db_on_critical_ops(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """A demoted admin's 15-min token must not open the admin zone — role comes from the DB."""
    user = await create_user(db_session, role="admin")
    await authorize(user.email)
    assert (await client.get(USERS_URL)).status_code == 200

    user.role = "member"
    await db_session.commit()
    assert (await client.get(USERS_URL)).status_code == 403
