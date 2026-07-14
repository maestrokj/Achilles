"""Entity body statements: upsert by natural key, soft-delete flags, per-source counters."""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import Chunk, Entity


async def upsert_row(session: AsyncSession, values: dict[str, Any]) -> int:
    """Upsert the relational body by (source_id, source_type, source_entity_id) → entity id.

    Re-capture revives a soft-deleted row: snapshot semantics, the source presented
    the record again (deleted_at is written by Harvester reconciliation only).
    """
    stmt = pg_insert(Entity).values(**values, is_deleted=False)
    update_set: dict[str, Any] = {k: stmt.excluded[k] for k in values}
    update_set["is_deleted"] = sa.false()
    update_set["deleted_at"] = sa.null()
    result = await session.execute(
        stmt.on_conflict_do_update(constraint="uq_entities_native", set_=update_set).returning(
            Entity.id
        )
    )
    return result.scalar_one()


async def set_deleted(
    session: AsyncSession, entity_id: int, *, deleted: bool, deleted_at: datetime | None
) -> None:
    """Flip is_deleted on the body and mirror it onto chunks in the same transaction."""
    await session.execute(
        sa.update(Entity)
        .where(Entity.id == entity_id)
        .values(is_deleted=deleted, deleted_at=deleted_at)
    )
    await session.execute(
        sa.update(Chunk).where(Chunk.entity_id == entity_id).values(is_deleted=deleted)
    )


async def count_for_source(session: AsyncSession, source_id: int) -> int:
    """Live entities one source contributed to the graph (harvester.html table)."""
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Entity)
            .where(Entity.source_id == source_id, sa.not_(Entity.is_deleted))
        )
    ) or 0


async def counts_by_source(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """Live entity/chunk counters per source: {source_id: (entities, chunks)}."""
    rows = await session.execute(
        sa.select(
            Entity.source_id,
            sa.func.count(sa.distinct(Entity.id)),
            sa.func.count(Chunk.id),
        )
        .join(
            Chunk,
            sa.and_(Chunk.entity_id == Entity.id, sa.not_(Chunk.is_deleted)),
            isouter=True,
        )
        .where(sa.not_(Entity.is_deleted))
        .group_by(Entity.source_id)
    )
    return {source_id: (entities, chunks) for source_id, entities, chunks in rows}
