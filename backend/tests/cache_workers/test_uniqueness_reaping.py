"""Run uniqueness lock (partial UNIQUE) + stale-heartbeat reaping — integration.

No domain run-table exists in stage 1, so a test-only table declares the
platform run contract exactly the way consumers (sync_runs, agent_runs…) do.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from achilles.infra import lifecycle
from achilles.infra.lifecycle import (
    run_state_check,
    uniqueness_lock_index,
)

pytestmark = [pytest.mark.integration]


class _TestBase(DeclarativeBase):
    pass


class DemoRun(_TestBase):
    __tablename__ = "demo_runs"
    __table_args__ = (
        run_state_check("demo_runs"),
        uniqueness_lock_index("demo_runs", "source_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Real consumers carry created_at (migration conventions) — the reaper's
    # default staleness anchor falls back to it for beat-less rows — and
    # finished_at, which the reaper stamps on the terminal write.
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


@pytest.fixture
async def demo_table(db_engine: AsyncEngine):
    async with db_engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all)
    yield
    async with db_engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.drop_all)


async def test_second_active_run_hits_the_lock(demo_table: None, db_session: AsyncSession):
    db_session.add(DemoRun(source_id=1, state="running"))
    await db_session.commit()

    db_session.add(DemoRun(source_id=1, state="queued"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_terminal_run_releases_the_lock(demo_table: None, db_session: AsyncSession):
    db_session.add(DemoRun(source_id=1, state="succeeded"))
    await db_session.commit()
    db_session.add(DemoRun(source_id=1, state="queued"))
    await db_session.commit()  # no conflict: the lock covers active states only


async def test_double_tick_starts_exactly_one_run(demo_table: None, db_session: AsyncSession):
    """The scheduler's double tick dies on the Postgres lock, not on Redis magic."""

    async def tick() -> int:
        stmt = pg_insert(DemoRun).values(source_id=7, state="queued").on_conflict_do_nothing()
        result = await db_session.execute(stmt)
        return cast("sa.CursorResult[Any]", result).rowcount or 0

    first, second = await tick(), await tick()
    await db_session.commit()
    assert (first, second) == (1, 0)


async def test_reaper_marks_dead_heartbeats_stale(
    demo_table: None, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    now = datetime.now(UTC)
    old = now - timedelta(minutes=10)
    db_session.add(DemoRun(source_id=1, state="running", heartbeat_at=old))
    db_session.add(DemoRun(source_id=2, state="running", heartbeat_at=now))
    # Died before the first beat (lost publish / killed at start): the anchor
    # falls back to created_at, the lock must not be held forever.
    db_session.add(DemoRun(source_id=3, state="running", heartbeat_at=None, created_at=old))
    # queued is outside the default spec scope — untouched.
    db_session.add(DemoRun(source_id=4, state="queued", heartbeat_at=None, created_at=old))
    await db_session.commit()

    monkeypatch.setattr(lifecycle, "RUN_TABLES", (lifecycle.RunTableSpec("demo_runs"),))
    swept = await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()
    assert swept == 2

    rows = (
        await db_session.execute(sa.select(DemoRun.source_id, DemoRun.state, DemoRun.finished_at))
    ).all()
    assert {row[0]: row[1] for row in rows} == {1: "stale", 2: "running", 3: "stale", 4: "queued"}
    # A reaped run is terminal — finished_at stamped; untouched rows stay NULL.
    assert {row[0]: row[2] is not None for row in rows} == {
        1: True,
        2: False,
        3: True,
        4: False,
    }


async def test_reaper_sweeps_queued_when_the_spec_says_so(
    demo_table: None, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A lock that covers queued needs a spec that reaps queued (curation_runs shape).

    Queued rows have no beat until a worker picks the job up, so they age on
    the backlog allowance, not stale_after: a healthy row waiting out the lane
    backlog survives, a genuinely lost publish is still reaped.
    """
    now = datetime.now(UTC)
    waiting = now - timedelta(minutes=10)  # inside QUEUED_ZOMBIE_AFTER — healthy backlog
    lost = now - lifecycle.QUEUED_ZOMBIE_AFTER - timedelta(minutes=1)
    db_session.add(DemoRun(source_id=1, state="queued", heartbeat_at=None, created_at=waiting))
    db_session.add(DemoRun(source_id=2, state="queued", heartbeat_at=None, created_at=lost))
    await db_session.commit()

    spec = lifecycle.RunTableSpec("demo_runs", active_states=("queued", "running"))
    monkeypatch.setattr(lifecycle, "RUN_TABLES", (spec,))
    swept = await lifecycle.reap_stale_runs(db_session, now=now)
    await db_session.commit()

    assert swept == 1
    rows = (await db_session.execute(sa.select(DemoRun.source_id, DemoRun.state))).all()
    assert {row[0]: row[1] for row in rows} == {1: "queued", 2: "stale"}
