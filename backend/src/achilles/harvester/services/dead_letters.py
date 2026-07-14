"""dead_letters work-queue ops (data-model.html#dead-letters-table).

One row per item; a repeat failure bumps attempts instead of stacking rows.
Resolution is deletion — any successful pass over the item clears its row,
so a reconciliation sweep drains the queue without a dedicated retry.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.harvester.models import DeadLetter

ERROR_TAIL = 500  # keep error_detail bounded


async def record(
    session: AsyncSession,
    *,
    source_id: int,
    run_id: int | None,
    source_type: str,
    source_entity_id: str,
    reason: str,
    error_detail: str | None = None,
) -> None:
    stmt = pg_insert(DeadLetter).values(
        source_id=source_id,
        run_id=run_id,
        source_type=source_type,
        source_entity_id=source_entity_id,
        reason=reason,
        error_detail=(error_detail or "")[:ERROR_TAIL] or None,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_dead_letters_item",
            set_={
                "attempts": DeadLetter.attempts + 1,
                "reason": stmt.excluded.reason,
                "error_detail": stmt.excluded.error_detail,
                "run_id": stmt.excluded.run_id,
            },
        )
    )


async def resolve(
    session: AsyncSession, *, source_id: int, source_type: str, source_entity_id: str
) -> None:
    """A successful capture of the item clears its row (resolved rows are deleted)."""
    await session.execute(
        sa.delete(DeadLetter).where(
            DeadLetter.source_id == source_id,
            DeadLetter.source_type == source_type,
            DeadLetter.source_entity_id == source_entity_id,
        )
    )


async def count_for_source(session: AsyncSession, source_id: int) -> int:
    """The DLQ pill on the sources table (harvester.html#sync-overview)."""
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(DeadLetter)
            .where(DeadLetter.source_id == source_id)
        )
    ) or 0


async def list_for_source(
    session: AsyncSession, source_id: int, *, limit: int = 200
) -> list[DeadLetter]:
    result = await session.scalars(
        sa.select(DeadLetter)
        .where(DeadLetter.source_id == source_id)
        .order_by(DeadLetter.id.desc())
        .limit(limit)
    )
    return list(result)


async def items_for_source(session: AsyncSession, source_id: int) -> list[dict[str, str]]:
    """The retry scope payload: every queued item as {source_type, source_entity_id}."""
    rows = await session.execute(
        sa.select(DeadLetter.source_type, DeadLetter.source_entity_id).where(
            DeadLetter.source_id == source_id
        )
    )
    return [
        {"source_type": source_type, "source_entity_id": source_entity_id}
        for source_type, source_entity_id in rows
    ]
