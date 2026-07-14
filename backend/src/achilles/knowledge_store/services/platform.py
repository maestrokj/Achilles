"""platform_settings singleton access (sync-modes.html#scheduling).

Seeded by the migration; the app reads/updates, never inserts. The Admin
"Platform" screen is the editor (admin/routes.py).
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import PlatformSettings

SINGLETON_ID = 1


async def get_platform_settings(session: AsyncSession) -> PlatformSettings:
    row = await session.scalar(
        sa.select(PlatformSettings).where(PlatformSettings.id == SINGLETON_ID)
    )
    if row is None:  # pragma: no cover — the migration seeds the row
        msg = "platform_settings singleton missing; run migrations"
        raise RuntimeError(msg)
    return row
