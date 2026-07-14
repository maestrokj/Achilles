"""Telegram admin section: RBAC, masked secrets, live getMe probe, webhook lifecycle (API)."""

import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import settings as app_settings
from achilles.telegram.constants import (
    CODE_TELEGRAM_WEBHOOK_FAILED,
    CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC,
)
from achilles.telegram.models import TelegramSettings
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user
from tests.telegram.conftest import EXPECTED_WEBHOOK_URL

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/telegram"

_GET_ME = r".*/getMe$"
_SET_WEBHOOK = r".*/setWebhook$"
_DELETE_WEBHOOK = r".*/deleteWebhook$"
_GET_WEBHOOK_INFO = r".*/getWebhookInfo$"


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
        "bot_username": None,
        "bot_token_mask": None,
        "webhook_secret_set": False,
        "last_test_ok": None,
        "last_test_at": None,
    }


async def test_admin_cannot_write_owner_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    assert (await client.patch(URL, json={"enabled": True})).status_code == 403

    # Owner may write; with no bot token yet, enabling does not reach Telegram.
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


async def test_secrets_stored_encrypted_and_shown_as_mask(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json={"bot_token": "12345:secret-token-abcd"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_token_mask"].endswith("abcd")
    assert "12345" not in body["bot_token_mask"]

    row = await db_session.scalar(sa.select(TelegramSettings))
    assert row is not None
    token = row.bot_token_enc
    assert token is not None
    assert token.startswith("v1:")
    assert "secret" not in token


async def test_empty_string_clears_a_secret(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:secret"})
    resp = await client.patch(URL, json={"bot_token": ""})
    assert resp.json()["bot_token_mask"] is None


async def test_probe_success_stamps_username(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:secret"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(
            200, json={"ok": True, "result": {"id": 1, "username": "achilles_bot"}}
        )
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "bot_username": "achilles_bot", "error": None}
    shown = (await client.get(URL)).json()
    assert shown["last_test_ok"] is True and shown["last_test_at"] is not None
    assert shown["bot_username"] == "achilles_bot"


async def test_probe_failure_is_200_with_error(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:revoked"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": False, "description": "Unauthorized"})
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "Unauthorized"
    assert (await client.get(URL)).json()["last_test_ok"] is False


async def test_probe_without_token(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "bot_username": None, "error": "no_token"}


async def test_enabling_registers_the_webhook(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # Turning the bot on registers the webhook: getMe stamps the @handle and
    # setWebhook fills the generated secret, making the surface available.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    set_hook = hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )

    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 200
    assert set_hook.called
    body = resp.json()
    assert body["enabled"] is True
    assert body["webhook_secret_set"] is True
    assert body["bot_username"] == "ach_bot"

    row = await db_session.scalar(sa.select(TelegramSettings))
    assert row is not None and row.is_available is True


async def test_disabling_removes_the_webhook(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )
    delete_hook = hibp_clean.post(url__regex=_DELETE_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )
    await client.patch(URL, json={"enabled": True})

    resp = await client.patch(URL, json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert delete_hook.called


async def test_rotating_token_while_enabled_reregisters_webhook(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # A running bot whose token is swapped (switch left on) must re-register at
    # Telegram — setWebhook binds to a token, so the new bot gets no updates
    # otherwise while the chip still reads Connected.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:old"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    set_hook = hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )
    await client.patch(URL, json={"enabled": True})
    assert set_hook.call_count == 1

    resp = await client.patch(URL, json={"bot_token": "67890:new"})
    assert resp.status_code == 200
    assert set_hook.call_count == 2


async def test_explicit_null_enabled_does_not_touch_webhook(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # PATCH {"enabled": null} is a no-op on the switch; it must not fire a
    # spurious setWebhook/deleteWebhook.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    set_hook = hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )
    delete_hook = hibp_clean.post(url__regex=_DELETE_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )
    resp = await client.patch(URL, json={"enabled": None})
    assert resp.status_code == 200
    assert not set_hook.called
    assert not delete_hook.called


def test_is_available_needs_switch_and_credentials():
    row = TelegramSettings(enabled=True, bot_token_enc="v1:x", webhook_secret_enc="v1:y")
    assert row.is_available is True
    for missing in ("enabled", "bot_token_enc", "webhook_secret_enc"):
        broken = TelegramSettings(enabled=True, bot_token_enc="v1:x", webhook_secret_enc="v1:y")
        setattr(broken, missing, None if missing != "enabled" else False)
        assert broken.is_available is False, missing


async def test_enable_rejected_when_base_url_not_public(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
):
    # A localhost instance can't receive a webhook: enabling must fail loudly and
    # leave the switch off, not stick on green with nothing behind it.
    monkeypatch.setattr(app_settings, "public_base_url", "http://localhost:3000")
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    set_hook = hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": True, "result": True})
    )

    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 409
    assert resp.json()["code"] == CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC
    assert not set_hook.called  # never even reached Telegram

    shown = (await client.get(URL)).json()
    assert shown["enabled"] is False
    assert shown["last_test_ok"] is False


async def test_enable_rolls_back_when_setwebhook_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # Telegram refuses setWebhook (e.g. a revoked token): the switch rolls back
    # and the admin gets the reason, not a silent half-on state.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:revoked"})
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    hibp_clean.post(url__regex=_SET_WEBHOOK).mock(
        return_value=Response(200, json={"ok": False, "description": "Unauthorized"})
    )

    resp = await client.patch(URL, json={"enabled": True})
    assert resp.status_code == 409
    assert resp.json()["code"] == CODE_TELEGRAM_WEBHOOK_FAILED
    assert "Unauthorized" in resp.json()["detail"]

    shown = (await client.get(URL)).json()
    assert shown["enabled"] is False
    assert shown["last_test_ok"] is False
    assert shown["webhook_secret_set"] is False  # secret stored only on success


async def _enable_directly(db_session: AsyncSession) -> None:
    """Flip enabled=true in the row without going through the connect flow, so a
    probe can be tested in isolation from setWebhook."""
    await db_session.execute(sa.text("UPDATE telegram_settings SET enabled = true WHERE id = 1"))
    await db_session.commit()


async def test_probe_flags_missing_webhook_when_enabled(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # An enabled bot with a valid token but no registered webhook is NOT connected:
    # the probe reads delivery from getWebhookInfo, not merely getMe.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    await _enable_directly(db_session)
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    hibp_clean.post(url__regex=_GET_WEBHOOK_INFO).mock(
        return_value=Response(200, json={"ok": True, "result": {"url": ""}})
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "webhook_missing"
    assert (await client.get(URL)).json()["last_test_ok"] is False


async def test_probe_ok_when_webhook_registered(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    hibp_clean: respx.MockRouter,
):
    # getWebhookInfo reports our expected URL → delivery is genuinely wired → ok.
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"bot_token": "12345:live"})
    await _enable_directly(db_session)
    hibp_clean.post(url__regex=_GET_ME).mock(
        return_value=Response(200, json={"ok": True, "result": {"id": 1, "username": "ach_bot"}})
    )
    hibp_clean.post(url__regex=_GET_WEBHOOK_INFO).mock(
        return_value=Response(200, json={"ok": True, "result": {"url": EXPECTED_WEBHOOK_URL}})
    )

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert (await client.get(URL)).json()["last_test_ok"] is True
