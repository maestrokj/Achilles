"""Notifications test scaffold.

Seeded rows (builtin channels + the route matrix) are reset by UPDATE/DELETE,
never truncated; the event journal and deliveries are wiped per test.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.auth.security.crypto import encrypt
from achilles.config import Settings
from achilles.notifications import webhooks
from achilles.notifications.models import NotificationChannel
from tests.auth.integration.conftest import (
    authorize,
    clean_state,
    db_engine,
    db_session,
    hibp_clean,
    login,
    outbox,
    redis_durable,
)
from tests.conftest import FlushRedis

__all__ = [
    "authorize",
    "clean_state",
    "db_engine",
    "db_session",
    "hibp_clean",
    "login",
    "outbox",
    "redis_durable",
]

WEBHOOK_URL = "https://hooks.example.test/achilles"
WEBHOOK_SECRET = "hmac-secret"

RESET_NOTIFICATIONS = (
    # journal + deliveries (broadcast rows carry no user FK — the users
    # truncate does not cascade into them)
    "TRUNCATE notifications RESTART IDENTITY CASCADE",
    # non-builtin channels go, builtin ones reset to the seeded state
    "DELETE FROM notification_channels WHERE NOT is_builtin",
    "UPDATE notification_channels SET enabled = true, last_test_ok = NULL, last_test_at = NULL",
    "UPDATE notification_routes SET enabled = true",
    # budget-tick tests flip the platform singleton — pin it back per test
    "UPDATE platform_settings SET ai_monthly_budget = NULL, ai_budget_alert_enabled = false",
)


@pytest.fixture(autouse=True)
async def notifications_clean(
    clean_state: None, db_engine: AsyncEngine, flush_redis: FlushRedis
) -> None:
    del clean_state, flush_redis  # ordering: run after the shared auth reset
    async with db_engine.begin() as conn:
        for statement in RESET_NOTIFICATIONS:
            await conn.execute(sa.text(statement))


@pytest.fixture(autouse=True)
def stub_webhook_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Webhook tests use a fake host and mock the transport; resolve it to a
    public IP so the SSRF guard passes. Guard tests override this per-test."""

    async def _resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]  # documentation-range public address

    monkeypatch.setattr(webhooks, "_resolve_host", _resolve)


async def create_webhook_channel(
    session: AsyncSession,
    test_settings: Settings,
    *,
    preset: str = "generic",
    name: str = "Ops",
    url: str = WEBHOOK_URL,
    secret: str | None = WEBHOOK_SECRET,
    enabled: bool = True,
    seed_routes: bool = True,
) -> NotificationChannel:
    """A webhook channel the way the admin dialog would create it."""
    key = test_settings.derived_crypto_key()
    channel = NotificationChannel(
        kind="webhook",
        preset=preset,
        name=name,
        url_enc=encrypt(url, key=key),
        secret_enc=encrypt(secret, key=key) if secret else None,
        enabled=enabled,
    )
    session.add(channel)
    await session.flush()
    if seed_routes:
        # Broadcast types only — targeted events never travel over webhooks.
        await session.execute(
            sa.text(
                "INSERT INTO notification_routes (event_type, channel_id, enabled) "
                "SELECT t.event_type, :channel_id, true FROM (VALUES ('sync'), ('security'),"
                " ('budget'), ('system'), ('discovery')) AS t(event_type)"
            ),
            {"channel_id": channel.id},
        )
    await session.commit()
    return channel
