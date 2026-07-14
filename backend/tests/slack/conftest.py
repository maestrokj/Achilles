"""Slack test scaffold: the QE table set (dialogue + KS + auth folded in) + the
slack_settings singleton reset (migration-seeded — reset by UPDATE, never truncated)."""

import hashlib
import hmac

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.auth.security.crypto import derive_crypto_key, encrypt
from achilles.config import Settings
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

RESET_SLACK_SETTINGS = """
UPDATE slack_settings SET
    team = NULL, team_name = NULL, bot_token_enc = NULL, signing_secret_enc = NULL,
    bot_user_id = NULL, enabled = false, auto_link_by_email = true,
    last_test_ok = NULL, last_test_at = NULL
WHERE id = 1
"""


TEAM = "T123"
BOT_USER = "UBOT"
BOT_TOKEN = "xoxb-test-token"
SIGNING_SECRET = "test-signing-secret"


@pytest.fixture(autouse=True)
def hf_offline(hibp_clean: respx.MockRouter) -> None:
    """The assigned model's tokenizer degrades to chars/4 — no HF egress in tests."""
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(return_value=Response(404))


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(QE_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
        await conn.execute(sa.text(RESET_SLACK_SETTINGS))
    await flush_redis()


async def configure_slack(
    session: AsyncSession, test_settings: Settings, *, enabled: bool = True, probed: bool = True
) -> None:
    """Fill the singleton the way the admin section would: secrets encrypted.

    ``probed=False`` mirrors the state before a successful "Test connection":
    token + secret + enabled are set, but the workspace facts (team, bot_user_id)
    a probe stamps are still NULL — so ``is_available`` is false.
    """
    key = derive_crypto_key(
        crypto_key=test_settings.crypto_key, secret_key=test_settings.secret_key
    )
    await session.execute(
        sa.text(
            "UPDATE slack_settings SET team = :team, team_name = :team_name, "
            "bot_token_enc = :token, signing_secret_enc = :secret, "
            "bot_user_id = :bot, enabled = :enabled WHERE id = 1"
        ),
        {
            "team": TEAM if probed else None,
            "team_name": "Acme" if probed else None,
            "token": encrypt(BOT_TOKEN, key=key),
            "secret": encrypt(SIGNING_SECRET, key=key),
            "bot": BOT_USER if probed else None,
            "enabled": enabled,
        },
    )
    await session.commit()


def sign(body: bytes, *, timestamp: str, secret: str = SIGNING_SECRET) -> dict[str, str]:
    """Headers of a properly signed Slack request."""
    digest = hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256)
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={digest.hexdigest()}",
        "Content-Type": "application/json",
    }
