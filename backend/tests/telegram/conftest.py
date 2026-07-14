"""Telegram test scaffold: the QE table set (dialogue + KS + auth folded in) + the
telegram_settings singleton reset (migration-seeded — reset by UPDATE, never truncated)."""

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.auth.security.crypto import derive_crypto_key, encrypt
from achilles.config import Settings
from achilles.config import settings as app_settings
from tests.auth.integration.conftest import (
    authorize,
    db_engine,
    db_session,
    hibp_clean,
    login,
    redis_durable,
)
from tests.conftest import FlushRedis
from tests.factories.ai import reset_ai_catalog
from tests.query_engine.conftest import QE_TABLES

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]

RESET_TELEGRAM_SETTINGS = """
UPDATE telegram_settings SET
    bot_token_enc = NULL, webhook_secret_enc = NULL, bot_username = NULL,
    enabled = false, last_test_ok = NULL, last_test_at = NULL
WHERE id = 1
"""


BOT_TOKEN = "12345:test-token"
WEBHOOK_SECRET = "test-webhook-secret"
BOT_USERNAME = "achilles_test_bot"


PUBLIC_BASE_URL = "https://achilles.test"
EXPECTED_WEBHOOK_URL = f"{PUBLIC_BASE_URL}/api/v1/telegram/webhook"


@pytest.fixture(autouse=True)
def hf_offline(hibp_clean: respx.MockRouter) -> None:
    """The assigned model's tokenizer degrades to chars/4 — no HF egress in tests."""
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(return_value=Response(404))


@pytest.fixture(autouse=True)
def telegram_public_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """setWebhook needs a public HTTPS base; the test default is localhost.

    The global settings singleton drives the webhook URL, so point it at a public
    host for the whole suite. The non-public path has its own test that overrides
    this back to localhost.
    """
    monkeypatch.setattr(app_settings, "public_base_url", PUBLIC_BASE_URL)


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(QE_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
        await conn.execute(sa.text(RESET_TELEGRAM_SETTINGS))
    await flush_redis()


async def configure_telegram(
    session: AsyncSession, test_settings: Settings, *, enabled: bool = True, available: bool = True
) -> None:
    """Fill the singleton the way the admin section would: secrets encrypted.

    ``available=False`` mirrors a half-wired surface: the webhook secret is set
    (so the secret gate passes) but the bot token is missing, so ``is_available``
    is false and the hook stays silent past the secret check.
    """
    key = derive_crypto_key(
        crypto_key=test_settings.crypto_key, secret_key=test_settings.secret_key
    )
    await session.execute(
        sa.text(
            "UPDATE telegram_settings SET bot_token_enc = :token, "
            "webhook_secret_enc = :secret, bot_username = :username, "
            "enabled = :enabled WHERE id = 1"
        ),
        {
            "token": encrypt(BOT_TOKEN, key=key) if available else None,
            "secret": encrypt(WEBHOOK_SECRET, key=key),
            "username": BOT_USERNAME if available else None,
            "enabled": enabled,
        },
    )
    await session.commit()


def secret_headers(secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    """Headers of a properly authenticated Telegram webhook request."""
    return {
        "X-Telegram-Bot-Api-Secret-Token": secret,
        "Content-Type": "application/json",
    }
