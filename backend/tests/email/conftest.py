"""Email test scaffold: the auth integration fixtures + the smtp_settings reset.

The singleton is migration-seeded — the shared clean_state resets it by UPDATE
(never TRUNCATE); SMTP itself is mocked at the aiosmtplib boundary
(email/_workzone/tests.html).
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

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


@pytest.fixture(autouse=True)
async def org_locale_ru(clean_state: None, db_engine: AsyncEngine) -> None:
    """Letter-language tests flip the org locale — pin the default back per test."""
    del clean_state  # ordering: run after the shared truncate/reset
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("UPDATE platform_settings SET locale = 'ru'"))
