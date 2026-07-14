"""Slack admin section: RBAC, masked secrets, encrypted storage, live test probe (API)."""

import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.slack.constants import SLACK_API_BASE_URL
from achilles.slack.models import SlackSettings
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/slack"


async def _login_owner(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)


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
        "auto_link_by_email": True,
        "team": None,
        "team_name": None,
        "bot_user_id": None,
        "bot_token_mask": None,
        "signing_secret_set": False,
        "last_test_ok": None,
        "last_test_at": None,
    }


async def test_admin_cannot_write_owner_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    assert (await client.patch(URL, json={"enabled": True})).status_code == 403

    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


async def test_auto_link_toggle_defaults_on_and_flips(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    assert (await client.get(URL)).json()["auto_link_by_email"] is True

    resp = await client.patch(URL, json={"auto_link_by_email": False})
    assert resp.status_code == 200
    assert resp.json()["auto_link_by_email"] is False

    row = await db_session.scalar(sa.select(SlackSettings))
    assert row is not None
    assert row.auto_link_by_email is False


async def test_secrets_stored_encrypted_and_shown_as_mask(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.patch(
        URL, json={"bot_token": "xoxb-secret-token-abcd", "signing_secret": "sig-secret"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_token_mask"].endswith("abcd")
    assert "xoxb" not in body["bot_token_mask"]
    assert body["signing_secret_set"] is True

    row = await db_session.scalar(sa.select(SlackSettings))
    assert row is not None
    assert row.bot_token_enc.startswith("v1:")
    assert "secret" not in row.bot_token_enc
    assert row.signing_secret_enc.startswith("v1:")


async def test_empty_string_clears_a_secret(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "xoxb-secret"})
    resp = await client.patch(URL, json={"bot_token": ""})
    assert resp.json()["bot_token_mask"] is None


async def test_probe_success_stamps_workspace_facts(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "xoxb-secret"})
    hibp_clean.post(f"{SLACK_API_BASE_URL}/auth.test").mock(
        return_value=Response(
            200, json={"ok": True, "team_id": "T123", "team": "Acme", "user_id": "U99"}
        )
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "team": "T123",
        "team_name": "Acme",
        "bot_user_id": "U99",
        "error": None,
    }
    shown = (await client.get(URL)).json()
    assert shown["last_test_ok"] is True and shown["last_test_at"] is not None


async def test_probe_failure_is_200_with_error(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "xoxb-revoked"})
    hibp_clean.post(f"{SLACK_API_BASE_URL}/auth.test").mock(
        return_value=Response(200, json={"ok": False, "error": "invalid_auth"})
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "invalid_auth"
    assert (await client.get(URL)).json()["last_test_ok"] is False


async def test_probe_without_token(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": False,
        "team": None,
        "team_name": None,
        "bot_user_id": None,
        "error": "no_token",
    }


def test_is_available_needs_switch_and_credentials():
    row = SlackSettings(enabled=True, team="T1", bot_token_enc="v1:x", signing_secret_enc="v1:y")
    assert row.is_available is True
    for missing in ("enabled", "team", "bot_token_enc", "signing_secret_enc"):
        broken = SlackSettings(
            enabled=True, team="T1", bot_token_enc="v1:x", signing_secret_enc="v1:y"
        )
        setattr(broken, missing, None if missing != "enabled" else False)
        assert broken.is_available is False, missing
