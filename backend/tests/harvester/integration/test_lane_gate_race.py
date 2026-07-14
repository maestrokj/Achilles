"""Lane-gate write-skew: the two facing gates never both pass (P0).

Without the shared advisory lock, two concurrent transactions — sync
mark_running and curation open_destructive_window — could each see the
other's NOT EXISTS as satisfied and both commit (write skew). The xact-scoped
pg_advisory_xact_lock serializes them: at most one wins.
"""

import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from achilles.harvester.constants import SyncMode, SyncTrigger
from achilles.harvester.services import sync_runs
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import CurationRun
from achilles.knowledge_store.services import curation
from tests.factories.knowledge import create_source

pytestmark = [pytest.mark.integration, pytest.mark.p0]

ROUNDS = 10


async def test_facing_gates_never_both_pass(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    for _ in range(ROUNDS):
        # Fresh queued sync run + running curation run each round.
        sync_id = await sync_runs.start_run(
            db_session,
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.SCHEDULE),
        )
        curation_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MANUAL))
        await curation.mark_running(db_session, curation_id)
        await db_session.commit()

        async def try_sync(run_id: int) -> bool:
            async with factory() as session, session.begin():
                return await sync_runs.mark_running(session, run_id)

        async def try_merge(run_id: int) -> bool:
            async with factory() as session, session.begin():
                return await curation.open_destructive_window(session, run_id)

        sync_won, merge_won = await asyncio.gather(try_sync(sync_id), try_merge(curation_id))
        assert not (sync_won and merge_won), "write skew: both gates passed"
        assert sync_won or merge_won  # and the lock never deadlocks them both out

        # Reset for the next round.
        await db_session.execute(
            sa.text("UPDATE sync_runs SET state = 'cancelled' WHERE state IN ('queued','running')")
        )
        await db_session.execute(
            sa.update(CurationRun).values(
                state=str(CurationState.CANCELLED), destructive_since=None
            )
        )
        await db_session.commit()
