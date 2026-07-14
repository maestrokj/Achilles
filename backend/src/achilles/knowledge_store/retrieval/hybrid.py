"""Assembled hybrid result in one call — the RAG-route contract for Query Engine.

Embed → vector ∥ lexical → graph one hop from the top text hits → sql when
filters came → RRF. A silent embedder degrades to the text/graph/sql lists
(degraded=True), never fails the search. The hidden-ACL probe answers the
grounding access-hint with content-free coordinates only
(query-engine/_workzone/grounding.html#access-hints) — a separate `hidden_hint`
call the caller makes off the answer's critical path, and only when the reply
cited nothing (the sole case the plaque is shown). The result carries the query
vector back so that lazy probe reuses it instead of re-embedding.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.services import embeddings_client
from achilles.ai_foundation.services.tokenizer import approx_counter
from achilles.ai_foundation.services.usage import record_usage
from achilles.knowledge_store.constants import (
    FTS_CONFIG,
    HIDDEN_PROBE_K,
    HYBRID_GRAPH_SEEDS,
    MAX_TOP_K,
)
from achilles.knowledge_store.models import Chunk, Entity, Identity, SourcePrincipal
from achilles.knowledge_store.retrieval import graph, lexical, sql, vector
from achilles.knowledge_store.retrieval.acl import acl_prefilter
from achilles.knowledge_store.retrieval.fusion import FusedHit, rrf
from achilles.knowledge_store.retrieval.hits import Hit

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HiddenHint:
    """Content-free coordinates of an ACL-hidden top candidate — never its content."""

    source_type: str
    author_email: str | None


@dataclass(frozen=True, slots=True)
class HybridResult:
    hits: list[FusedHit]
    degraded: bool  # embedder was silent → no vector list in the fusion
    # The embedded query, handed back so a later hidden_hint() probe reuses it
    # instead of paying the embedder twice; None when the embedder degraded.
    query_vector: list[float] | None
    embedding_model: str | None


async def embed_query(session: AsyncSession, query: str) -> tuple[list[float], str] | None:
    """Embed one search query and record the spend as query_rag; None = degrade.

    The online-search embedding is unattributed platform spend on the
    assigned embedding model (cost-accounting.html) — recorded here so every
    caller (hybrid, the bare vector route) counts it exactly once.
    """
    result = await embeddings_client.embed(session, [query])
    if result is None:
        return None
    await record_usage(
        session,
        model_pk=result.model.id,
        function=AiFunction.QUERY_RAG,
        input_tokens=result.prompt_tokens or approx_counter(query),
    )
    return result.vectors[0], result.model.model_id


async def search(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    top_k: int,
    filters: sql.SqlFilters | None = None,
    allowed_source_ids: Sequence[int] | None = None,
) -> HybridResult:
    # API-key scope restricts inside every primitive's SQL — a post-filter over
    # the fused list would eat into top_k and skew the ranking.
    top_k = min(top_k, MAX_TOP_K)
    embedded = await embed_query(session, query)

    vector_hits: list[Hit] = []
    if embedded is not None:
        query_vector, model_id = embedded
        vector_hits = await vector.search(
            session,
            user_id=user_id,
            query_vector=query_vector,
            embedding_model=model_id,
            top_k=top_k,
            allowed_source_ids=allowed_source_ids,
        )
    lexical_hits = await lexical.search(
        session, user_id=user_id, query=query, top_k=top_k, allowed_source_ids=allowed_source_ids
    )

    ranked: list[list[Hit]] = [hits for hits in (vector_hits, lexical_hits) if hits]

    seeds = list(
        dict.fromkeys(hit.entity_id for hits in (vector_hits, lexical_hits) for hit in hits)
    )[:HYBRID_GRAPH_SEEDS]
    if seeds:
        graph_hits = await graph.search(
            session,
            user_id=user_id,
            start_ids=seeds,
            depth=1,
            top_k=top_k,
            allowed_source_ids=allowed_source_ids,
        )
        if graph_hits:
            ranked.append(graph_hits)

    if filters is not None:
        sql_hits = await sql.search(
            session,
            user_id=user_id,
            filters=filters,
            top_k=top_k,
            allowed_source_ids=allowed_source_ids,
        )
        if sql_hits:
            ranked.append(sql_hits)

    fused = rrf(ranked, top_k=top_k)
    return HybridResult(
        hits=fused,
        degraded=embedded is None,
        query_vector=embedded[0] if embedded else None,
        embedding_model=embedded[1] if embedded else None,
    )


async def hidden_hint(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    query_vector: list[float] | None,
    embedding_model: str | None,
) -> HiddenHint | None:
    """Would an ACL-hidden entity have made the unfiltered top? Coordinates only.

    Two lean probes (lexical + vector) without the ACL JOIN give candidate
    entity ids; the ones failing the pre-filter are hidden. The best-ranked
    hidden entity yields source_type + author email via the identity bridge —
    a pointer for "request access", not a leak.
    """
    candidate_ids = await _unfiltered_top(
        session, query=query, query_vector=query_vector, embedding_model=embedding_model
    )
    if not candidate_ids:
        return None
    visible = set(
        (
            await session.execute(
                sa.select(Entity.id).where(
                    Entity.id.in_(candidate_ids), acl_prefilter(Entity.id, user_id)
                )
            )
        ).scalars()
    )
    hidden = [entity_id for entity_id in candidate_ids if entity_id not in visible]
    if not hidden:
        return None
    row = (
        await session.execute(
            sa.select(Entity.source_type, SourcePrincipal.email, Identity.email)
            .outerjoin(SourcePrincipal, SourcePrincipal.id == Entity.author_principal_id)
            .outerjoin(Identity, Identity.id == SourcePrincipal.identity_id)
            .where(Entity.id == hidden[0])
        )
    ).first()
    if row is None:
        return None
    source_type, principal_email, identity_email = row
    return HiddenHint(source_type=source_type, author_email=principal_email or identity_email)


async def _unfiltered_top(
    session: AsyncSession,
    *,
    query: str,
    query_vector: list[float] | None,
    embedding_model: str | None,
) -> list[int]:
    """Top entity ids with no ACL — rank order preserved, lexical then vector."""
    tsquery = sa.func.websearch_to_tsquery(FTS_CONFIG, query)
    lexical_stmt = (
        sa.select(Chunk.entity_id)
        .join(Entity, Entity.id == Chunk.entity_id)
        .where(
            Chunk.text_tsv.op("@@")(tsquery),
            sa.not_(Chunk.is_deleted),
            sa.not_(Entity.is_deleted),
        )
        .order_by(sa.func.ts_rank(Chunk.text_tsv, tsquery).desc(), Chunk.id)
        .limit(HIDDEN_PROBE_K)
    )
    ids: list[int] = list((await session.execute(lexical_stmt)).scalars())
    if query_vector is not None:
        distance = Chunk.embedding.cosine_distance(query_vector)
        vector_stmt = (
            sa.select(Chunk.entity_id)
            .join(Entity, Entity.id == Chunk.entity_id)
            .where(
                Chunk.embedding.is_not(None),
                Chunk.embedding_model == embedding_model,
                sa.not_(Chunk.is_deleted),
                sa.not_(Entity.is_deleted),
            )
            .order_by(distance, Chunk.id)
            .limit(HIDDEN_PROBE_K)
        )
        ids += list((await session.execute(vector_stmt)).scalars())
    return list(dict.fromkeys(ids))
