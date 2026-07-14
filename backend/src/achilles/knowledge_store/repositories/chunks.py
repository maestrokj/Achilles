"""Chunk-set statements: diff-apply against the chunker output (data-model.html#chunks)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import Chunk
from achilles.knowledge_store.services.chunking import ChunkDraft


async def apply_diff(session: AsyncSession, entity_id: int, drafts: list[ChunkDraft]) -> None:
    """Sync the chunk set to the drafts by (ordinal, content_hash): touch only what changed.

    One upsert + one delete regardless of the draft count — this is the Loader
    hot path (Harvester calls it per captured record, stage 5). The upsert WHERE
    keeps unchanged live rows untouched, while an unchanged-content re-capture of
    a soft-deleted entity still revives its chunks — the mirror of the body
    revive in upsert_row.
    """
    if drafts:
        stmt = pg_insert(Chunk).values(
            [
                {
                    "entity_id": entity_id,
                    "ordinal": d.ordinal,
                    "text": d.text,
                    "token_count": d.token_count,
                    "content_hash": d.content_hash,
                }
                for d in drafts
            ]
        )
        # Text change invalidates the vector (re-embed only on text change);
        # a revive of unchanged content keeps its perfectly valid embedding.
        text_changed = Chunk.content_hash.is_distinct_from(stmt.excluded.content_hash)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_chunks_entity_ordinal",
            set_={
                "text": stmt.excluded.text,
                "token_count": stmt.excluded.token_count,
                "content_hash": stmt.excluded.content_hash,
                "is_deleted": False,
                "embedding": sa.case((text_changed, None), else_=Chunk.embedding),
                "embedding_model": sa.case((text_changed, None), else_=Chunk.embedding_model),
            },
            where=sa.or_(text_changed, Chunk.is_deleted),
        )
        await session.execute(stmt)

    surplus = sa.delete(Chunk).where(Chunk.entity_id == entity_id)
    if drafts:
        surplus = surplus.where(Chunk.ordinal.notin_([d.ordinal for d in drafts]))
    await session.execute(surplus)
    await session.flush()
