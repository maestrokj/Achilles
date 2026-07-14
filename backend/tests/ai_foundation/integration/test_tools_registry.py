"""tools table constraints: UNIQUE(name), source/access dictionaries (tests.html, P1)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.models import Tool

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_duplicate_name_bounces(db_session: AsyncSession) -> None:
    db_session.add(Tool(name="web_search"))  # seeded by the migration
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_unknown_source_bounces(db_session: AsyncSession) -> None:
    db_session.add(Tool(name="rogue", source="webhook"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_unknown_access_bounces(db_session: AsyncSession) -> None:
    db_session.add(Tool(name="rogue", access="admin"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
