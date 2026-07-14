"""SMTP admin section: RBAC, masked password, encrypted storage, inline test (API)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.email import smtp
from achilles.email.constants import TransientSendError
from achilles.email.models import SmtpSettings
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/smtp"

FULL_CONFIG = {
    "host": "smtp.company.com",
    "port": 587,
    "security": "starttls",
    "username": "mailer",
    "password": "smtp-password-abcd",
    "from_address": "Achilles <no-reply@company.com>",
    "is_enabled": True,
}


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
        "is_enabled": False,
        "host": None,
        "port": None,
        "security": "starttls",
        "username": None,
        "password_mask": None,
        "from_address": None,
        "is_available": False,
        "last_test_ok": None,
        "last_test_at": None,
    }


async def test_admin_cannot_write_owner_can(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    assert (await client.patch(URL, json={"is_enabled": True})).status_code == 403

    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json=FULL_CONFIG)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_enabled"] is True
    assert body["is_available"] is True


async def test_password_stored_encrypted_and_shown_as_mask(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.patch(URL, json=FULL_CONFIG)
    body = resp.json()
    assert body["password_mask"].endswith("abcd")
    assert "smtp-password" not in body["password_mask"]

    row = await db_session.scalar(sa.select(SmtpSettings))
    assert row is not None
    assert row.password_enc.startswith("v1:")
    assert "smtp-password" not in row.password_enc


async def test_empty_string_clears_the_password(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    await client.patch(URL, json={"password": "something"})
    resp = await client.patch(URL, json={"password": ""})
    assert resp.json()["password_mask"] is None


async def test_availability_needs_the_switch_and_the_fields(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    partial = {k: v for k, v in FULL_CONFIG.items() if k != "is_enabled"}
    assert (await client.patch(URL, json=partial)).json()["is_available"] is False
    assert (await client.patch(URL, json={"is_enabled": True})).json()["is_available"] is True
    assert (await client.patch(URL, json={"host": ""})).json()["is_available"] is False


async def test_bad_security_and_port_are_422(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    assert (await client.patch(URL, json={"security": "tls13"})).status_code == 422
    assert (await client.patch(URL, json={"port": 0})).status_code == 422


async def test_inline_test_success_stamps_last_test(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    monkeypatch: pytest.MonkeyPatch,
):
    sent: list[str] = []

    async def fake_send(
        row: object, *, key: bytes, to: str, composed: object, send_timeout: float
    ) -> None:
        sent.append(to)

    monkeypatch.setattr(smtp, "send", fake_send)
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)
    await client.patch(URL, json=FULL_CONFIG)

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error": None}
    assert sent == [owner.email], "the letter goes to the acting admin"
    shown = (await client.get(URL)).json()
    assert shown["last_test_ok"] is True and shown["last_test_at"] is not None


async def test_inline_test_failure_is_200_with_error(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_send(*args: object, **kwargs: object) -> None:
        raise TransientSendError("connection refused")

    monkeypatch.setattr(smtp, "send", fake_send)
    await _login_owner(db_session, authorize)
    await client.patch(URL, json=FULL_CONFIG)

    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "refused" in resp.json()["error"]
    assert (await client.get(URL)).json()["last_test_ok"] is False


async def test_inline_test_unconfigured(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    await _login_owner(db_session, authorize)
    resp = await client.post(f"{URL}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "not_configured"}


async def test_platform_settings_reflects_smtp(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """The invites tab gates on this flag — it must follow smtp_settings."""
    await _login_owner(db_session, authorize)
    assert (await client.get("/api/v1/admin/settings")).json()["smtp_configured"] is False
    await client.patch(URL, json=FULL_CONFIG)
    assert (await client.get("/api/v1/admin/settings")).json()["smtp_configured"] is True
