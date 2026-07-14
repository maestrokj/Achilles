"""Vector primitive: ANN by cosine over chunks.embedding (halfvec + partial HNSW).

Semantics and cross-language matching live here; exact names/IDs are the
lexical primitive's job. Vectors of different models are incomparable — the
query is embedded by the assigned model and only chunks it embedded qualify.

hnsw.iterative_scan (pgvector 0.8+) keeps the scan going until the LIMIT
survives local quals; the ACL JOIN remains the one predicate it cannot
compensate — recall on real data is a stage-5 measurement
(data-model.html#chunks decision box).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.constants import MAX_TOP_K
from achilles.knowledge_store.models import Chunk, Entity
from achilles.knowledge_store.retrieval.acl import acl_prefilter, source_scope
from achilles.knowledge_store.retrieval.hits import Hit


async def search(
    session: AsyncSession,
    *,
    user_id: int,
    query_vector: Sequence[float],
    embedding_model: str,
    top_k: int,
    allowed_source_ids: Sequence[int] | None = None,
) -> list[Hit]:
    await session.execute(sa.text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = Chunk.embedding.cosine_distance(list(query_vector)).label("distance")
    stmt = (
        sa.select(Chunk.id, Chunk.entity_id, distance)
        .join(Entity, Entity.id == Chunk.entity_id)
        .where(
            Chunk.embedding.is_not(None),
            Chunk.embedding_model == embedding_model,
            sa.not_(Chunk.is_deleted),  # partial-HNSW predicate
            sa.not_(Entity.is_deleted),
            acl_prefilter(Chunk.entity_id, user_id),
            *source_scope(Entity.source_id, allowed_source_ids),
        )
        .order_by(distance, Chunk.id)
        .limit(min(top_k, MAX_TOP_K))
    )
    rows = await session.execute(stmt)
    return [
        Hit(entity_id=entity_id, score=1.0 - float(dist), chunk_id=chunk_id)
        for chunk_id, entity_id, dist in rows
    ]
