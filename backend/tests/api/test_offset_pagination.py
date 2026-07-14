"""Offset contract for admin tables: count + clamp + deterministic slice.

Runs against the real DB (users as the row source) — the mechanic is one
count + one offset SELECT, so the fixtures mirror the auth integration set.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.pagination import OffsetParams, offset_page
from achilles.auth.models import User
from tests.auth.integration.conftest import clean_state, db_engine, db_session
from tests.factories.users import build_user

__all__ = ["clean_state", "db_engine", "db_session"]

pytestmark = [pytest.mark.api, pytest.mark.p1]


async def _seed_users(session: AsyncSession, count: int) -> None:
    session.add_all(build_user() for _ in range(count))
    await session.commit()


def _stmt() -> sa.Select[tuple[User]]:
    return sa.select(User).order_by(User.id)


async def test_pages_cover_everything_once(db_session: AsyncSession):
    await _seed_users(db_session, 30)
    stmt = _stmt()

    first, total, page = await offset_page(db_session, stmt, OffsetParams(1, 25))
    second, _, page2 = await offset_page(db_session, stmt, OffsetParams(2, 25))

    assert (total, page, page2) == (30, 1, 2)
    ids = [u.id for u in first] + [u.id for u in second]
    assert ids == sorted(ids), "ordering must be deterministic"
    assert len(ids) == len(set(ids)) == 30, "no duplicates, no losses"


async def test_past_the_end_page_clamps_to_last(db_session: AsyncSession):
    await _seed_users(db_session, 30)

    items, total, page = await offset_page(db_session, _stmt(), OffsetParams(99, 25))

    assert (total, page) == (30, 2), "page must clamp to the last one, not return empty"
    assert len(items) == 5


async def test_empty_table_is_page_one(db_session: AsyncSession):
    items, total, page = await offset_page(db_session, _stmt(), OffsetParams(7, 25))

    assert (items, total, page) == ([], 0, 1)


async def test_count_override_drives_the_window(db_session: AsyncSession):
    await _seed_users(db_session, 30)
    count_stmt = sa.select(sa.func.count()).select_from(User)

    items, total, page = await offset_page(
        db_session, _stmt(), OffsetParams(2, 25), count_stmt=count_stmt
    )

    assert (total, page) == (30, 2)
    assert len(items) == 5
