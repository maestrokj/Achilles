"""Storage metrics for the Admin "Knowledge Store" screen (knowledge-store.html).

One aggregate query per projection; the optional source filter recomputes every
counter as that source's contribution. Vector volume is the stored embedding
bytes (pg_column_size over live rows) — the HNSW index on top is excluded, so
the number reads as "data", not "data + acceleration structures".
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from achilles.ai_foundation.models import AiModel
from achilles.ai_foundation.services import embeddings_client
from achilles.knowledge_store.models import Chunk, Entity, EntityEdge, EntityRef


@dataclass(frozen=True, slots=True)
class GraphCounts:
    entities: int
    chunks: int
    edges: int


@dataclass(frozen=True, slots=True)
class GraphMetrics:
    entities: int
    chunks: int
    edges: int
    pending_refs: int  # entity_ref staging rows — the growing-tail signal, not an error
    vector_bytes: int


async def graph_counts(session: AsyncSession) -> GraphCounts:
    """The cheap platform-wide triple (dashboard tile) — no vector scan, no refs."""
    entities, chunks, edges = (
        await session.execute(
            sa.select(
                sa.select(sa.func.count()).select_from(Entity).scalar_subquery(),
                sa.select(sa.func.count())
                .select_from(Chunk)
                .where(Chunk.is_deleted.is_(False))
                .scalar_subquery(),
                sa.select(sa.func.count()).select_from(EntityEdge).scalar_subquery(),
            )
        )
    ).one()
    return GraphCounts(entities=int(entities), chunks=int(chunks), edges=int(edges))


async def graph_metrics(session: AsyncSession, *, source_id: int | None = None) -> GraphMetrics:
    src = aliased(Entity)

    entities_stmt = sa.select(sa.func.count()).select_from(Entity)
    chunks_stmt = sa.select(sa.func.count()).select_from(Chunk).where(Chunk.is_deleted.is_(False))
    vector_stmt = (
        sa.select(
            sa.func.coalesce(
                sa.func.sum(sa.func.pg_column_size(Chunk.embedding)).filter(
                    Chunk.embedding.is_not(None)
                ),
                0,
            )
        )
        .select_from(Chunk)
        .where(Chunk.is_deleted.is_(False))
    )
    edges_stmt = sa.select(sa.func.count()).select_from(EntityEdge)
    refs_stmt = sa.select(sa.func.count()).select_from(EntityRef)
    if source_id is not None:
        # The joins exist only for the filter — Postgres cannot prune inner joins.
        entities_stmt = entities_stmt.where(Entity.source_id == source_id)
        chunk_join = (Entity, Entity.id == Chunk.entity_id)
        chunks_stmt = chunks_stmt.join(*chunk_join).where(Entity.source_id == source_id)
        vector_stmt = vector_stmt.join(*chunk_join).where(Entity.source_id == source_id)
        # An edge belongs to the source of its src node — a simple, explainable cut.
        edges_stmt = edges_stmt.join(src, src.id == EntityEdge.src_entity_id).where(
            src.source_id == source_id
        )
        refs_stmt = refs_stmt.join(src, src.id == EntityRef.src_entity_id).where(
            src.source_id == source_id
        )

    # One round-trip: every counter rides as a self-contained scalar subquery.
    entities, chunks, edges, refs, vector_bytes = (
        await session.execute(
            sa.select(
                entities_stmt.scalar_subquery(),
                chunks_stmt.scalar_subquery(),
                edges_stmt.scalar_subquery(),
                refs_stmt.scalar_subquery(),
                vector_stmt.scalar_subquery(),
            )
        )
    ).one()
    return GraphMetrics(
        entities=int(entities),
        chunks=int(chunks),
        edges=int(edges),
        pending_refs=int(refs),
        vector_bytes=int(vector_bytes),
    )


async def reembed_progress(
    session: AsyncSession,
    *,
    model: AiModel | None = None,
) -> tuple[int, int] | None:
    """(done, total) over live chunks against the assigned embedder; None = no embedder.

    Same predicate as curation_steps.reembed_batches — the numbers read as
    "how far the refresh got", the KS screen and the dashboard both show them.

    `model` lets a caller that already holds the embedder (the KS panel
    resolves once for progress + model names; the assignment PATCH counts
    against the not-yet-committed model) skip the 3-table join here.
    """
    if model is None:
        assigned = await embeddings_client.resolve_assigned(session)
        if assigned is None:
            return None
        model, _provider = assigned
    total, pending = (
        await session.execute(
            sa.select(
                sa.func.count(),
                sa.func.count().filter(
                    sa.or_(
                        Chunk.embedding.is_(None),
                        Chunk.embedding_model.is_distinct_from(model.model_id),
                    )
                ),
            )
            .select_from(Chunk)
            .where(Chunk.is_deleted.is_(False))
        )
    ).one()
    return int(total) - int(pending), int(total)


async def reembed_model_names(
    session: AsyncSession,
    *,
    model: AiModel | None = None,
) -> tuple[str | None, str | None]:
    """(from, to) display names of the refresh — the panel's "old → new" line.

    `to` is the assigned embedder; `from` is the dominant stale embedding_model
    among live chunks, resolved to its catalog display name (the raw model_id
    when the catalog row is gone). Either side is None when nothing answers.

    `model` reuses an already-resolved embedder (see `reembed_progress`).
    """
    if model is None:
        assigned = await embeddings_client.resolve_assigned(session)
        if assigned is None:
            return None, None
        model, _provider = assigned
    stale = await session.scalar(
        sa.select(Chunk.embedding_model)
        .where(
            Chunk.is_deleted.is_(False),
            Chunk.embedding_model.is_not(None),
            Chunk.embedding_model != model.model_id,
        )
        .group_by(Chunk.embedding_model)
        .order_by(sa.func.count().desc())
        .limit(1)
    )
    from_name = None
    if stale is not None:
        from_name = (
            await session.scalar(sa.select(AiModel.display_name).where(AiModel.model_id == stale))
            or stale
        )
    return from_name, model.display_name
