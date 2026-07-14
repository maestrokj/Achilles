"""Mattermost admin section: RBAC, masked secrets, live /users/me probe, atomic enable (API)."""

import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.mattermost.constants import CODE_MATTERMOST_ENABLE_FAILED
from achilles.mattermost.models import MattermostSettings
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user
from tests.mattermost.conftest import BASE_URL

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/mattermost"

_USERS_ME = r".*/api/v4/users/me$"

ME_OK = Response(200, json={"id": "bot-user-1", "username": "achilles"})
ME_REFUSED = Response(
    401, json={"id": "api.context.session_expired", "message": "Invalid or expired session token"}
)


async def _login_owner(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)


async def _saved(client: AsyncClient) -> None:
    """Owner saves a plausible address + token, the probe not yet run."""
    await client.patch(URL, json={"base_url": BASE_URL, "bot_token": "mm-live-token"})


async def test_member_cannot_read_admin_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403

    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": False,
        "base_url": None,
        "bot_username": None,
        "bot_token_mask": None,
        "listener_connected": None,
        "last_test_ok": None,
        "last_test_at": None,
    }


async def test_admin_cannot_write_owner_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    assert (await client.patch(URL, json={"enabled": True})).status_code == 403

    # Owner may write; with nothing saved yet, enabling does not reach a server.
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


async def test_secrets_stored_encrypted_and_shown_as_mask(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"bot_token": "mm-secret-token-abcd"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_token_mask"].endswith("abcd")
    assert "mm-secret" not in body["bot_token_mask"]

    row = await db_session.scalar(sa.select(MattermostSettings))
    assert row is not None
    token = row.bot_token_enc
    assert token is not None
    assert token.startswith("v1:")
    assert "secret" not in token


async def test_empty_string_clears_a_secret(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "mm-secret"})
    resp = await client.patch(URL, json={"bot_token": ""})
    assert resp.json()["bot_token_mask"] is None


async def test_base_url_is_validated_and_normalized(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    assert (await client.patch(URL, json={"base_url": "ftp://mm.test"})).status_code == 422
    assert (await client.patch(URL, json={"base_url": "not a url"})).status_code == 422

    resp = await client.patch(URL, json={"base_url": "https://mm.company.test/"})
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "https://mm.company.test"  # trailing slash trimmed


async def test_private_lan_address_is_accepted(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    # Deliberate non-goal of an SSRF guard: Achilles is self-hosted and the
    # Mattermost server legitimately lives on a private LAN — the listener dials
    # out, nothing dials in (mattermost/schemas.py docstring).
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"base_url": "http://10.0.0.5:8065"})
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "http://10.0.0.5:8065"


async def test_probe_success_stamps_identity(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await _saved(client)
    hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_OK)

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "bot_username": "achilles", "error": None}
    shown = (await client.get(URL)).json()
    assert shown["last_test_ok"] is True and shown["last_test_at"] is not None
    assert shown["bot_username"] == "achilles"

    row = await db_session.scalar(sa.select(MattermostSettings))
    assert row is not None and row.bot_user_id == "bot-user-1"


async def test_probe_failure_is_200_with_error(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await _saved(client)
    hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_REFUSED)

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "Invalid or expired session token"
    assert (await client.get(URL)).json()["last_test_ok"] is False


async def test_probe_names_what_is_missing(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.post(f"{URL}/test")
    assert resp.json() == {"ok": False, "bot_username": None, "error": "no_base_url"}

    await client.patch(URL, json={"base_url": BASE_URL})
    resp = await client.post(f"{URL}/test")
    assert resp.json() == {"ok": False, "bot_username": None, "error": "no_token"}


async def test_enabling_probes_the_token_and_sticks(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # Turning the bot on live-proves the token: /users/me stamps the identity
    # (bot_user_id is what makes the surface available and lets the listener
    # tell the bot's own posts apart).
    await _login_owner(db_session, authorize)
    await _saved(client)
    me = hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_OK)

    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 200
    assert me.called
    body = resp.json()
    assert body["enabled"] is True
    assert body["bot_username"] == "achilles"
    assert body["last_test_ok"] is True

    row = await db_session.scalar(sa.select(MattermostSettings))
    assert row is not None and row.is_available is True


async def test_enable_rolls_back_when_the_server_refuses(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # The server refuses /users/me (revoked token): the switch rolls back and
    # the admin gets the reason, not a silent half-on state.
    await _login_owner(db_session, authorize)
    await _saved(client)
    hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_REFUSED)

    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 409
    assert resp.json()["code"] == CODE_MATTERMOST_ENABLE_FAILED
    assert "Invalid or expired session token" in resp.json()["detail"]

    shown = (await client.get(URL)).json()
    assert shown["enabled"] is False
    assert shown["last_test_ok"] is False


async def test_rotating_the_token_while_enabled_reprobes(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # A running bot whose token is swapped (switch left on) must re-prove the new
    # token — the stamped identity belongs to the old pair and is cleared.
    await _login_owner(db_session, authorize)
    await _saved(client)
    me = hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_OK)
    await client.patch(URL, json={"enabled": True})
    assert me.call_count == 1

    resp = await client.patch(URL, json={"bot_token": "mm-rotated-token"})
    assert resp.status_code == 200
    assert me.call_count == 2


async def test_explicit_null_enabled_does_not_probe(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # PATCH {"enabled": null} is a no-op on the switch; it must not fire a
    # spurious probe against the server.
    await _login_owner(db_session, authorize)
    await _saved(client)
    me = hibp_clean.get(url__regex=_USERS_ME).mock(return_value=ME_OK)

    resp = await client.patch(URL, json={"enabled": None})
    assert resp.status_code == 200
    assert not me.called


def test_is_available_needs_switch_address_token_and_identity():
    row = MattermostSettings(
        enabled=True, base_url="http://mm.test", bot_token_enc="v1:x", bot_user_id="u1"
    )
    assert row.is_available is True
    for missing in ("enabled", "base_url", "bot_token_enc", "bot_user_id"):
        broken = MattermostSettings(
            enabled=True, base_url="http://mm.test", bot_token_enc="v1:x", bot_user_id="u1"
        )
        setattr(broken, missing, None if missing != "enabled" else False)
        assert broken.is_available is False, missing
