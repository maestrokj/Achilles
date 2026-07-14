"""Session management: list active devices, revoke one, revoke the others.

Design: auth-security/_wireframes/session-management.html — a session is a
refresh-token family; the one behind the cookie is flagged current.
"""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import RefreshToken
from achilles.auth.security.tokens import generate_token, hash_token
from tests.auth.integration.conftest import AuthorizeFn, LoginFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

SESSIONS_URL = "/api/v1/auth/sessions"
REVOKE_OTHERS_URL = "/api/v1/auth/sessions/revoke-others"
REFRESH_URL = "/api/v1/auth/refresh"


async def _two_sessions(db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn) -> str:
    """Open one session, then a second that becomes the client's current one. Returns email."""
    user = await create_user(db_session)
    await login(user.email)  # family 1 — cookie replaced by the next login
    await authorize(user.email)  # family 2 — current (cookie + bearer on the client)
    return user.email


async def test_list_marks_the_current_session(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    await _two_sessions(db_session, login, authorize)

    resp = await client.get(SESSIONS_URL)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert sum(1 for s in items if s["is_current"]) == 1


async def test_revoke_other_session(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    await _two_sessions(db_session, login, authorize)
    items = (await client.get(SESSIONS_URL)).json()["items"]
    other = next(s for s in items if not s["is_current"])

    resp = await client.delete(f"{SESSIONS_URL}/{other['id']}")
    assert resp.status_code == 204

    remaining = (await client.get(SESSIONS_URL)).json()["items"]
    assert [s["id"] for s in remaining] == [next(s["id"] for s in items if s["is_current"])]
    # The current cookie still refreshes.
    assert (await client.post(REFRESH_URL)).status_code == 200


async def test_revoke_current_session_clears_cookie(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    await _two_sessions(db_session, login, authorize)
    current = next(s for s in (await client.get(SESSIONS_URL)).json()["items"] if s["is_current"])

    resp = await client.delete(f"{SESSIONS_URL}/{current['id']}")
    assert resp.status_code == 204
    assert "Max-Age=0" in resp.headers["set-cookie"]
    assert (await client.post(REFRESH_URL)).status_code == 401


async def test_revoke_others_keeps_current(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    email = await _two_sessions(db_session, login, authorize)
    await login(email)  # a third family, still not current

    assert (await client.post(REVOKE_OTHERS_URL)).status_code == 204

    items = (await client.get(SESSIONS_URL)).json()["items"]
    assert len(items) == 1
    assert items[0]["is_current"] is True
    assert (await client.post(REFRESH_URL)).status_code == 200


async def test_revoke_unknown_session_is_404(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    resp = await client.delete(f"{SESSIONS_URL}/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["code"] == "SESSION_NOT_FOUND"


async def test_cannot_revoke_another_users_session(
    client: AsyncClient, db_session: AsyncSession, login: LoginFn, authorize: AuthorizeFn
):
    victim = await create_user(db_session)
    await login(victim.email)
    victim_family = await db_session.scalar(
        sa.select(RefreshToken.family_id).where(RefreshToken.user_id == victim.id)
    )

    attacker = await create_user(db_session)
    await authorize(attacker.email)
    resp = await client.delete(f"{SESSIONS_URL}/{victim_family}")
    assert resp.status_code == 404
    # The victim's session is untouched.
    assert (
        await db_session.scalar(
            sa.select(sa.func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == victim.id)
        )
        == 1
    )


async def test_created_at_is_sign_in_not_last_refresh(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    user = await create_user(db_session)
    await authorize(user.email)
    before = (await client.get(SESSIONS_URL)).json()["items"][0]["created_at"]

    # A refresh rotates the token (a fresh row under the same family); the
    # session's start time must not jump forward to the rotation moment.
    assert (await client.post(REFRESH_URL)).status_code == 200
    after = (await client.get(SESSIONS_URL)).json()["items"][0]["created_at"]
    assert after == before


async def test_list_dedups_a_family_with_two_live_tokens(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """One family = one device, even if a rotation race leaked a second live
    token into it — the list counts the family once, not once per live token.
    """
    user = await create_user(db_session)
    await authorize(user.email)
    row = await db_session.scalar(sa.select(RefreshToken).where(RefreshToken.user_id == user.id))
    assert row is not None
    # A second live token in the same family — the shape the race would leave.
    db_session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(generate_token()),
            family_id=row.family_id,
            expires_at=row.expires_at,
            absolute_expires_at=row.absolute_expires_at,
            remember_me=row.remember_me,
            user_agent=row.user_agent,
            ip=row.ip,
        )
    )
    await db_session.commit()

    items = (await client.get(SESSIONS_URL)).json()["items"]
    assert len(items) == 1
    assert items[0]["is_current"] is True


async def test_sessions_require_auth(client: AsyncClient):
    assert (await client.get(SESSIONS_URL)).status_code == 401
    assert (await client.post(REVOKE_OTHERS_URL)).status_code == 401
