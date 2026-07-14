"""KS test scaffold: mirrors the auth integration conftest with the KS tables.

backup_settings is a migration-seeded singleton — it is reset by UPDATE, never
truncated. TRUNCATE of `users` cascades into `identity` anyway; the explicit
list keeps isolation independent of FK topology.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from tests.auth.integration.conftest import (
    AUTH_TABLES,
    authorize,
    db_engine,
    db_session,
    hibp_clean,
    login,
    redis_durable,
)
from tests.conftest import FlushRedis

from achilles.knowledge_store.services import backups

__all__ = ["authorize", "db_engine", "db_session", "hibp_clean", "login", "redis_durable"]

# Reverse-dependency order: KS tables first, then the auth set (composed, so a
# table added to the auth list never goes missing from KS isolation).
# model_assignments/model_usage are AI leaves KS writes through (embed-on-write,
# query_rag spend) — cleared here so an assignment never leaks between tests.
# Public: the query_engine conftest composes on top of this list.
KS_TABLES = (
    "model_assignments",
    "model_usage",
    "entity_acl",
    "group_membership",
    "entity_ref",
    "entity_edge",
    "chunks",
    "entities",
    "source_group",
    "source_principal",
    "identity",
    "curation_runs",
    "backup_snapshots",
    "dead_letters",
    "sync_runs",
    "sources",
    *AUTH_TABLES,
)


async def configure_backup_destination(session: AsyncSession, root: Path) -> None:
    """Point the backup singleton at a file:// destination under the test tmpdir."""
    settings_row = await backups.get_settings(session)
    settings_row.destination_url = (root / "backups").as_uri()
    await session.commit()


_RESET_BACKUP_SETTINGS = """
UPDATE backup_settings
SET destination_url = NULL, destination_creds_enc = NULL, frequency = 'daily',
    weekday = NULL, time = '02:00', retention_count = 14
WHERE id = 1
"""

RESET_PLATFORM_SETTINGS = """
UPDATE platform_settings
SET org_name = 'Achilles', org_logo_url = NULL, org_description = NULL,
    accent_color = '#6366f1', timezone = 'UTC', locale = 'ru', date_format = 'DD.MM.YYYY',
    access_token_ttl = 900, refresh_token_ttl = 2592000, session_absolute_ttl = 7776000,
    maintenance_mode = false, mcp_enabled = true,
    ai_monthly_budget = NULL, ai_budget_alert_enabled = false, chat_weekly_token_budget = NULL,
    sync_interval_minutes = 15, reconcile_minute_of_week = 8820,
    watchdog_silence_hours = 12,
    curation_frequency = 'daily', curation_weekday = NULL, curation_time = '04:00',
    agent_weekly_token_budget = NULL, agent_iteration_cap = 15, agent_max_concurrency = 4
WHERE id = 1
"""


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(KS_TABLES)} RESTART IDENTITY CASCADE"))
        await conn.execute(sa.text(_RESET_BACKUP_SETTINGS))
        await conn.execute(sa.text(RESET_PLATFORM_SETTINGS))
    await flush_redis()
