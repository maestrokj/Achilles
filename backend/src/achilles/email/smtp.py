"""SMTP transport: a composed letter → the wire, with an honest error taxonomy.

The password is decrypted only at the moment of send and never leaves this
module; 5xx verdicts are permanent (no retry), 4xx/network are transient
(delivery.html#errors).
"""

from email.message import EmailMessage

import aiosmtplib

from achilles.auth.security.crypto import decrypt
from achilles.email.compose import ComposedEmail
from achilles.email.constants import PermanentSendError, SmtpSecurity, TransientSendError
from achilles.email.models import SmtpSettings

_PERMANENT_FLOOR = 500


def build_message(composed: ComposedEmail, *, to: str, from_address: str) -> EmailMessage:
    """multipart/alternative: plain text first, HTML as the rich alternative."""
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = to
    message["Subject"] = composed.subject
    message.set_content(composed.text)
    message.add_alternative(composed.html, subtype="html")
    return message


async def send(
    row: SmtpSettings, *, key: bytes, to: str, composed: ComposedEmail, send_timeout: float
) -> None:
    """One delivery over the settings row; raises Permanent/TransientSendError."""
    if not (row.is_available and row.host and row.port and row.from_address):
        raise TransientSendError("SMTP is not configured")
    message = build_message(composed, to=to, from_address=row.from_address)
    password = decrypt(row.password_enc, key=key) if row.password_enc else None
    security = SmtpSecurity(row.security)
    try:
        await aiosmtplib.send(
            message,
            hostname=row.host,
            port=row.port,
            username=row.username,
            password=password,
            use_tls=security is SmtpSecurity.SSL_TLS,
            start_tls=True if security is SmtpSecurity.STARTTLS else False,  # noqa: SIM210 — None means "opportunistic" in aiosmtplib
            timeout=send_timeout,
        )
    except aiosmtplib.SMTPRecipientsRefused as exc:
        # aiosmtplib raises this for ANY non-250/251 RCPT verdict — a 4xx
        # (greylisting «450 try again later») is transient, only 5xx is final.
        # No verdicts at all reads as refused-for-good: fail closed, no retry.
        if exc.recipients and all(r.code < _PERMANENT_FLOOR for r in exc.recipients):
            raise TransientSendError(str(exc)) from exc
        raise PermanentSendError(str(exc)) from exc
    except aiosmtplib.SMTPResponseException as exc:
        if exc.code >= _PERMANENT_FLOOR:
            raise PermanentSendError(f"{exc.code} {exc.message}") from exc
        raise TransientSendError(f"{exc.code} {exc.message}") from exc
    except (aiosmtplib.SMTPException, OSError) as exc:
        raise TransientSendError(str(exc)) from exc
