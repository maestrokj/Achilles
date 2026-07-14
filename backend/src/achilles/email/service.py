"""smtp_settings singleton access, patch and the inline test send."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import User
from achilles.auth.security.crypto import encrypt_optional, mask_encrypted
from achilles.email import smtp
from achilles.email.compose import DEFAULT_PRODUCT_NAME, Branding, compose
from achilles.email.constants import (
    SMTP_TEST_TIMEOUT_SECONDS,
    EmailKind,
    PermanentSendError,
    TransientSendError,
)
from achilles.email.i18n import Locale, resolve_locale
from achilles.email.models import SmtpSettings
from achilles.email.schemas import SmtpSettingsOut, SmtpSettingsPatch, SmtpTestOut
from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services import platform

SINGLETON_ID = 1


async def get_settings(session: AsyncSession) -> SmtpSettings:
    row = await session.scalar(sa.select(SmtpSettings).where(SmtpSettings.id == SINGLETON_ID))
    if row is None:  # pragma: no cover — the migration seeds the row
        msg = "smtp_settings singleton missing; run migrations"
        raise RuntimeError(msg)
    return row


async def smtp_available(session: AsyncSession) -> bool:
    """The one availability truth consumers gate on (invites, forgot, admin reset)."""
    return (await get_settings(session)).is_available


def _locale_of(row: PlatformSettings, reader: User | None) -> Locale:
    """The one fallback chain for text language: reader override -> org default."""
    return resolve_locale((reader.locale if reader else None) or row.locale)


def _branding_of(row: PlatformSettings) -> Branding:
    """The workspace face every letter wears — org name + accent colour."""
    # Trim so a stray space an admin left in the org name never reaches a subject line.
    return Branding(
        product_name=row.org_name.strip() or DEFAULT_PRODUCT_NAME, accent_color=row.accent_color
    )


async def reader_locale(session: AsyncSession, user: User) -> Locale:
    """The reader's language, for text that carries no branding (feed, webhooks)."""
    return _locale_of(await platform.get_platform_settings(session), user)


async def org_locale(session: AsyncSession) -> Locale:
    """The org-default language — text with no personal reader (invites, webhooks)."""
    return _locale_of(await platform.get_platform_settings(session), None)


async def letter_context(
    session: AsyncSession, reader: User | None = None
) -> tuple[Locale, Branding]:
    """Everything platform_settings owes a letter — language and branding, one read."""
    row = await platform.get_platform_settings(session)
    return _locale_of(row, reader), _branding_of(row)


def settings_out(row: SmtpSettings, *, key: bytes) -> SmtpSettingsOut:
    return SmtpSettingsOut(
        is_enabled=row.is_enabled,
        host=row.host,
        port=row.port,
        security=row.security,
        username=row.username,
        password_mask=mask_encrypted(row.password_enc, key=key),
        from_address=row.from_address,
        is_available=row.is_available,
        last_test_ok=row.last_test_ok,
        last_test_at=row.last_test_at,
    )


def apply_patch(row: SmtpSettings, body: SmtpSettingsPatch, *, key: bytes) -> None:
    fields = body.model_fields_set
    if "password" in fields:
        row.password_enc = encrypt_optional(body.password, key=key)
    for field in ("host", "port", "security", "username", "from_address"):
        if field in fields:
            setattr(row, field, getattr(body, field))
    if "is_enabled" in fields and body.is_enabled is not None:
        row.is_enabled = body.is_enabled


async def run_test(
    session: AsyncSession,
    row: SmtpSettings,
    *,
    key: bytes,
    to: str,
    locale: Locale,
    branding: Branding,
) -> SmtpTestOut:
    """Inline test letter to the acting admin: stamps last_test_*, never a 5xx."""
    out = await _probe(row, key=key, to=to, locale=locale, branding=branding)
    row.last_test_ok = out.ok
    row.last_test_at = datetime.now(UTC)
    await session.commit()
    return out


async def _probe(
    row: SmtpSettings, *, key: bytes, to: str, locale: Locale, branding: Branding
) -> SmtpTestOut:
    if not row.is_available:
        return SmtpTestOut(ok=False, error="not_configured")
    composed = compose(EmailKind.TEST, locale=locale, branding=branding)
    try:
        await smtp.send(
            row, key=key, to=to, composed=composed, send_timeout=SMTP_TEST_TIMEOUT_SECONDS
        )
    except (PermanentSendError, TransientSendError) as exc:
        return SmtpTestOut(ok=False, error=str(exc))
    return SmtpTestOut(ok=True)
