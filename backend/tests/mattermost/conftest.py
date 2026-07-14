"""Mattermost test scaffold: the QE table set (dialogue + KS + auth folded in) + the
mattermost_settings singleton reset (migration-seeded — reset by UPDATE, never truncated)."""

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

RESET_MATTERMOST_SETTINGS = """
UPDATE mattermost_settings SET
    base_url = NULL, bot_token_enc = NULL, bot_user_id = NULL, bot_username = NULL,
    enabled = false, last_test_ok = NULL, last_test_at = NULL
WHERE id = 1
"""


BASE_URL = "http://mattermost.test"
BOT_TOKEN = "mm-test-token"
BOT_USER_ID = "bot-user-1"
BOT_USERNAME = "achilles"


@pytest.fixture(autouse=True)
def hf_offline(hibp_clean: respx.MockRouter) -> None:
    """The assigned model's tokenizer degrades to chars/4 — no HF egress in tests."""
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(return_value=Response(404))


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(QE_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
        await conn.execute(sa.text(RESET_MATTERMOST_SETTINGS))
    await flush_redis()


async def configure_mattermost(
    session: AsyncSession, test_settings: Settings, *, enabled: bool = True, available: bool = True
) -> None:
    """Fill the singleton the way the admin section would: the token encrypted.

    ``available=False`` mirrors a half-wired surface: the address stands but the
    token is missing, so ``is_available`` is false and the job stays silent.
    """
    key = derive_crypto_key(
        crypto_key=test_settings.crypto_key, secret_key=test_settings.secret_key
    )
    await session.execute(
        sa.text(
            "UPDATE mattermost_settings SET base_url = :base_url, bot_token_enc = :token, "
            "bot_user_id = :user_id, bot_username = :username, enabled = :enabled WHERE id = 1"
        ),
        {
            "base_url": BASE_URL,
            "token": encrypt(BOT_TOKEN, key=key) if available else None,
            "user_id": BOT_USER_ID if available else None,
            "username": BOT_USERNAME if available else None,
            "enabled": enabled,
        },
    )
    await session.commit()
