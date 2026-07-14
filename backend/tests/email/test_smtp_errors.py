"""Error taxonomy at the aiosmtplib boundary: 5xx permanent, 4xx/network transient (unit)."""

import aiosmtplib
import pytest

from achilles.auth.security.crypto import derive_crypto_key, encrypt
from achilles.email import smtp
from achilles.email.compose import compose
from achilles.email.constants import EmailKind, PermanentSendError, TransientSendError
from achilles.email.i18n import Locale
from achilles.email.models import SmtpSettings

pytestmark = [pytest.mark.unit, pytest.mark.p1]

KEY = derive_crypto_key(crypto_key="", secret_key="unit-test-secret")


def settings_row(**overrides: object) -> SmtpSettings:
    values: dict[str, object] = {
        "id": 1,
        "host": "smtp.test",
        "port": 25,
        "security": "none",
        "from_address": "Achilles <no-reply@test.local>",
        "is_enabled": True,
    }
    values.update(overrides)
    return SmtpSettings(**values)


COMPOSED = compose(EmailKind.TEST, locale=Locale.EN)


async def _send(row: SmtpSettings) -> None:
    await smtp.send(row, key=KEY, to="to@example.com", composed=COMPOSED, send_timeout=1.0)


def test_is_available_needs_switch_and_required_fields():
    assert settings_row().is_available is True
    assert settings_row(is_enabled=False).is_available is False
    assert settings_row(host=None).is_available is False
    assert settings_row(port=None).is_available is False
    assert settings_row(from_address=None).is_available is False
    # The password is optional — an open relay / dev sink needs none.
    assert settings_row(password_enc=None).is_available is True


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (aiosmtplib.SMTPResponseException(550, "mailbox unavailable"), PermanentSendError),
        (aiosmtplib.SMTPResponseException(450, "try again later"), TransientSendError),
        (aiosmtplib.SMTPRecipientsRefused([]), PermanentSendError),
        # RCPT verdicts keep the 4xx/5xx split: greylisting retries, no-mailbox doesn't.
        (
            aiosmtplib.SMTPRecipientsRefused(
                [aiosmtplib.SMTPRecipientRefused(450, "greylisted", "to@example.com")]
            ),
            TransientSendError,
        ),
        (
            aiosmtplib.SMTPRecipientsRefused(
                [aiosmtplib.SMTPRecipientRefused(550, "no mailbox", "to@example.com")]
            ),
            PermanentSendError,
        ),
        (aiosmtplib.SMTPConnectError("refused"), TransientSendError),
        (OSError("network down"), TransientSendError),
        (aiosmtplib.SMTPServerDisconnected("gone"), TransientSendError),
    ],
)
async def test_error_taxonomy(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, expected: type[Exception]
):
    async def boom(*args: object, **kwargs: object) -> None:
        raise raised

    monkeypatch.setattr(aiosmtplib, "send", boom)
    with pytest.raises(expected):
        await _send(settings_row())


async def test_password_decrypted_only_at_send(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    async def capture(message: object, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(aiosmtplib, "send", capture)
    row = settings_row(password_enc=encrypt("smtp-password", key=KEY), username="mailer")
    await _send(row)
    assert seen["password"] == "smtp-password"
    assert seen["username"] == "mailer"
    assert seen["hostname"] == "smtp.test"


async def test_security_modes_map_to_tls_flags(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, object]] = []

    async def capture(message: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(aiosmtplib, "send", capture)
    await _send(settings_row(security="none"))
    await _send(settings_row(security="starttls"))
    await _send(settings_row(security="ssl_tls"))
    assert (calls[0]["use_tls"], calls[0]["start_tls"]) == (False, False)
    assert (calls[1]["use_tls"], calls[1]["start_tls"]) == (False, True)
    assert (calls[2]["use_tls"], calls[2]["start_tls"]) == (True, False)


async def test_unconfigured_row_is_transient(monkeypatch: pytest.MonkeyPatch):
    """A row switched off mid-flight: retryable, not a crash."""

    async def boom(*args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("must not reach the wire")

    monkeypatch.setattr(aiosmtplib, "send", boom)
    with pytest.raises(TransientSendError):
        await _send(settings_row(is_enabled=False))
