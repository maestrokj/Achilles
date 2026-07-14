"""telegram_settings singleton access, patch, live probe and webhook lifecycle."""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.security.crypto import decrypt, encrypt, encrypt_optional, mask_encrypted
from achilles.config import settings as app_settings
from achilles.telegram.client import TelegramApiError, TelegramBotClient
from achilles.telegram.constants import (
    CODE_TELEGRAM_WEBHOOK_FAILED,
    CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC,
    TEST_ERR_NETWORK,
    TEST_ERR_NO_TOKEN,
    TEST_ERR_WEBHOOK_MISSING,
    TEST_ERR_WEBHOOK_NOT_PUBLIC,
    WEBHOOK_PATH,
    WEBHOOK_SECRET_BYTES,
)
from achilles.telegram.models import TelegramSettings
from achilles.telegram.schemas import TelegramSettingsOut, TelegramSettingsPatch, TelegramTestOut

logger = logging.getLogger(__name__)

SINGLETON_ID = 1


def _username_of(data: dict[str, object]) -> str | None:
    """Pull the bot's @handle out of a getMe envelope (`{"result": {...}}`)."""
    result = data.get("result")
    username = result.get("username") if isinstance(result, dict) else None
    return str(username) if username else None


def _registered_url_of(info: dict[str, object]) -> str:
    """The url Telegram currently posts updates to (getWebhookInfo); '' if none."""
    result = info.get("result")
    url = result.get("url") if isinstance(result, dict) else None
    return str(url) if url else ""


def expected_webhook_url() -> str:
    """Where Telegram should deliver: the instance's public base + the webhook path."""
    return app_settings.public_url(WEBHOOK_PATH)


def webhook_base_is_public(base_url: str) -> bool:
    """Can Telegram actually reach this base URL to deliver updates?

    Telegram posts only to a public HTTPS host, so a plain-http or non-routable
    address (localhost, a private/loopback IP, a `.local` name) can never receive
    a webhook. Catching that here turns a silent no-delivery into an honest,
    actionable message instead of a green switch with nothing behind it.
    """
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        ip = ip_address(host)
    except ValueError:
        # A hostname, not a literal IP — reject the obvious non-routable names.
        return host != "localhost" and not host.endswith(".local")
    return ip.is_global  # routable public address — the same predicate fetch_url uses


async def get_settings(session: AsyncSession) -> TelegramSettings:
    row = await session.scalar(
        sa.select(TelegramSettings).where(TelegramSettings.id == SINGLETON_ID)
    )
    if row is None:  # pragma: no cover — the migration seeds the row
        msg = "telegram_settings singleton missing; run migrations"
        raise RuntimeError(msg)
    return row


def settings_out(row: TelegramSettings, *, key: bytes) -> TelegramSettingsOut:
    return TelegramSettingsOut(
        enabled=row.enabled,
        bot_username=row.bot_username,
        bot_token_mask=mask_encrypted(row.bot_token_enc, key=key),
        webhook_secret_set=bool(row.webhook_secret_enc),
        last_test_ok=row.last_test_ok,
        last_test_at=row.last_test_at,
    )


def apply_patch(row: TelegramSettings, body: TelegramSettingsPatch, *, key: bytes) -> None:
    fields = body.model_fields_set
    if "bot_token" in fields:
        row.bot_token_enc = encrypt_optional(body.bot_token, key=key)
    if "enabled" in fields and body.enabled is not None:
        row.enabled = body.enabled


async def run_test(session: AsyncSession, row: TelegramSettings, *, key: bytes) -> TelegramTestOut:
    """Live probe of real delivery readiness; stamps last_test_*, never a 5xx.

    ``last_test_ok`` means "Telegram is actually delivering to this bot", not
    merely "the token is valid" — so an enabled bot whose webhook isn't
    registered on a public URL reads as failed, not connected.
    """
    out = await _probe(row, key=key)
    if out.bot_username:
        row.bot_username = out.bot_username
    row.last_test_ok = out.ok
    row.last_test_at = datetime.now(UTC)
    await session.commit()
    return out


async def _probe(row: TelegramSettings, *, key: bytes) -> TelegramTestOut:
    if not row.bot_token_enc:
        return TelegramTestOut(ok=False, error=TEST_ERR_NO_TOKEN)
    client = TelegramBotClient(decrypt(row.bot_token_enc, key=key))
    try:
        username = _username_of(await client.get_me())
        # Bot switched off: the probe only vouches for the token — delivery N/A.
        if not row.enabled:
            return TelegramTestOut(ok=True, bot_username=username)
        # Bot on: delivery must be genuinely wired. A non-public base can't
        # receive updates at all; otherwise ask Telegram what it has registered.
        if not webhook_base_is_public(app_settings.public_base_url):
            return TelegramTestOut(
                ok=False, bot_username=username, error=TEST_ERR_WEBHOOK_NOT_PUBLIC
            )
        info = await client.get_webhook_info()
        if _registered_url_of(info) != expected_webhook_url():
            return TelegramTestOut(ok=False, bot_username=username, error=TEST_ERR_WEBHOOK_MISSING)
        return TelegramTestOut(ok=True, bot_username=username)
    except (TelegramApiError, httpx.HTTPError) as exc:
        error = exc.description if isinstance(exc, TelegramApiError) else TEST_ERR_NETWORK
        return TelegramTestOut(ok=False, error=error)
    finally:
        await client.aclose()


@dataclass(frozen=True)
class WebhookResult:
    """Outcome of a connect attempt: whether it succeeded and, if not, why.

    ``registered`` is True only when setWebhook actually ran — a tokenless enable
    is a benign no-op (ok, not registered) rather than a failure.
    """

    ok: bool
    registered: bool = False
    error_code: str | None = None
    detail: str = ""


def stamp_connect(row: TelegramSettings, result: WebhookResult) -> None:
    """Fold a connect outcome into the row's test state, mirroring run_test.

    A failed connect rolls the switch back — no delivery behind it — and stamps
    a failed probe; a real registration stamps success. A tokenless enable
    (ok but nothing registered) keeps the switch and stamps nothing.
    """
    if not result.ok:
        row.enabled = False
        row.last_test_ok = False
        row.last_test_at = datetime.now(UTC)
    elif result.registered:
        row.last_test_ok = True
        row.last_test_at = datetime.now(UTC)


async def connect_webhook(row: TelegramSettings, *, key: bytes) -> WebhookResult:
    """Register the webhook at Telegram (Achilles owns the generated secret).

    Mutates the row on success (bot_username, webhook_secret_enc) but does not
    commit or stamp last_test_* — the caller owns the transaction so enabling
    can be atomic: no registration, no enabled bot.
    """
    if not row.bot_token_enc:
        return WebhookResult(ok=True)  # nothing to register; the switch may still stand
    if not webhook_base_is_public(app_settings.public_base_url):
        return WebhookResult(
            ok=False,
            error_code=CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC,
            detail=app_settings.public_base_url,
        )
    secret = (
        decrypt(row.webhook_secret_enc, key=key)
        if row.webhook_secret_enc
        else secrets.token_urlsafe(WEBHOOK_SECRET_BYTES)
    )
    client = TelegramBotClient(decrypt(row.bot_token_enc, key=key))
    try:
        me = await client.get_me()
        await client.set_webhook(url=expected_webhook_url(), secret_token=secret)
    except (TelegramApiError, httpx.HTTPError) as exc:
        detail = exc.description if isinstance(exc, TelegramApiError) else "Telegram is unreachable"
        logger.warning("telegram setWebhook failed: %s", detail)
        return WebhookResult(ok=False, error_code=CODE_TELEGRAM_WEBHOOK_FAILED, detail=detail)
    finally:
        await client.aclose()
    row.bot_username = _username_of(me) or row.bot_username
    row.webhook_secret_enc = encrypt(secret, key=key)
    return WebhookResult(ok=True, registered=True)


async def disconnect_webhook(row: TelegramSettings, *, key: bytes) -> None:
    """Disabling the bot removes the webhook at Telegram (best-effort)."""
    if not row.bot_token_enc:
        return
    client = TelegramBotClient(decrypt(row.bot_token_enc, key=key))
    try:
        await client.delete_webhook()
    except (TelegramApiError, httpx.HTTPError) as exc:
        logger.warning("telegram deleteWebhook failed: %s", exc)
    finally:
        await client.aclose()
