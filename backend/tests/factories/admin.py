"""platform_settings helpers: the singleton is UPDATEd, never inserted (CHECK id=1)."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services.platform import get_platform_settings


async def set_platform_settings(session: AsyncSession, **values: Any) -> PlatformSettings:
    row = await get_platform_settings(session)
    for field, value in values.items():
        setattr(row, field, value)
    await session.commit()
    return row
