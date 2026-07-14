"""Lexical primitive: exact matches over chunks.text_tsv (config 'simple', ts_rank).

Semantics and cross-language matching are the vector primitive's job (stage 4);
this one exists for names, IDs, abbreviations and code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.constants import FTS_CONFIG, MAX_TOP_K
from achilles.knowledge_store.models import Chunk, Entity
from achilles.knowledge_store.retrieval.acl import acl_prefilter, source_scope
from achilles.knowledge_store.retrieval.hits import Hit


async def search(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    top_k: int,
    allowed_source_ids: Sequence[int] | None = None,
) -> list[Hit]:
    tsquery = sa.func.websearch_to_tsquery(FTS_CONFIG, query)
    score = sa.func.ts_rank(Chunk.text_tsv, tsquery).label("score")
    stmt = (
        sa.select(Chunk.id, Chunk.entity_id, score)
        .join(Entity, Entity.id == Chunk.entity_id)
        .where(
            Chunk.text_tsv.op("@@")(tsquery),
            sa.not_(Chunk.is_deleted),  # partial-GIN predicate
            sa.not_(Entity.is_deleted),
            acl_prefilter(Chunk.entity_id, user_id),
            *source_scope(Entity.source_id, allowed_source_ids),
        )
        .order_by(score.desc(), Chunk.id)
        .limit(min(top_k, MAX_TOP_K))
    )
    rows = await session.execute(stmt)
    return [
        Hit(entity_id=entity_id, score=float(rank), chunk_id=chunk_id)
        for chunk_id, entity_id, rank in rows
    ]
