"""Curation Pass steps (lifecycle.html#curation-pass).

Delivery (Harvester, per-source) and grooming (this module, platform-wide)
run side by side; only the destructive step — duplicate merge — takes the
lane gate. Every step is an absolute, idempotent recompute: a double run
converges to the same state.
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.services import embeddings_client
from achilles.ai_foundation.services.tokenizer import approx_counter
from achilles.ai_foundation.services.usage import record_usage
from achilles.knowledge_store.constants import (
    AUTHORITY_WEIGHT,
    CURATION_BATCH,
    DEMAND_LOG_WEIGHT,
    EMBED_BATCH_TIMEOUT_SECONDS,
    REEMBED_BATCH,
    REEMBED_LOADING_MAX_SECONDS,
    REEMBED_LOADING_POLL_SECONDS,
    REEMBED_RUNTIME_MAX_RETRIES,
    REEMBED_RUNTIME_WAIT_SECONDS,
    REL_TYPE_BY_RELATION,
    TRUST_HALF_LIFE_DAYS,
    AuthorityTier,
    EdgeOrigin,
    RelType,
)
from achilles.knowledge_store.models import Chunk, Entity, EntityEdge, EntityRef, Source
from achilles.knowledge_store.services.entities import soft_delete

logger = logging.getLogger(__name__)


class EmbeddingRuntimeUnavailableError(RuntimeError):
    """The embeddings runtime never answered within the re-embed's wait budget.

    Raised so run_reembed terminates the run as `failed` (honest: chunks stay
    stale) instead of `succeeded` with nothing re-embedded. The predicate is
    self-healing — a re-triggered model change resumes the tail.
    """


async def materialize_refs(session: AsyncSession) -> int:
    """entity_ref claims whose target has arrived → entity_edge, claim deleted.

    Deterministic: exact match on (target_ref = source_entity_id, target_kind =
    source_type), narrowed by source_hint → connector_type when present.
    Ambiguous claims (several matches) wait — guessing is not this step's job.
    Returns the number of edges materialized (lifecycle.html#edge-materialization).
    """
    total = 0
    last_id = 0
    while True:
        ref_ids = (
            await session.scalars(
                sa.select(EntityRef.id)
                .where(EntityRef.id > last_id)
                .order_by(EntityRef.id)
                .limit(CURATION_BATCH)
            )
        ).all()
        if not ref_ids:
            return total
        last_id = ref_ids[-1]
        # Set-based per batch: refs with exactly one live target (HAVING = 1
        # keeps the "ambiguous claims wait" rule; zero matches never group).
        matches = (
            await session.execute(
                sa.select(
                    EntityRef.id,
                    EntityRef.src_entity_id,
                    EntityRef.relation,
                    sa.func.min(Entity.id).label("target"),
                )
                .join(
                    Entity,
                    sa.and_(
                        Entity.source_entity_id == EntityRef.target_ref,
                        Entity.source_type == EntityRef.target_kind,
                        Entity.is_deleted.is_(False),
                        Entity.id != EntityRef.src_entity_id,
                    ),
                )
                .join(Source, Source.id == Entity.source_id)
                .where(
                    EntityRef.id.in_(ref_ids),
                    sa.or_(
                        EntityRef.source_hint.is_(None),
                        EntityRef.source_hint == "",
                        Source.connector_type == EntityRef.source_hint,
                    ),
                )
                .group_by(EntityRef.id, EntityRef.src_entity_id, EntityRef.relation)
                .having(sa.func.count() == 1)
            )
        ).all()
        if not matches:
            continue
        await session.execute(
            pg_insert(EntityEdge)
            .values(
                [
                    {
                        "src_entity_id": src_id,
                        "dst_entity_id": target,
                        "rel_type": str(REL_TYPE_BY_RELATION.get(relation, RelType.LINKS_TO)),
                        "origin": str(EdgeOrigin.CURATION),
                    }
                    for _, src_id, relation, target in matches
                ]
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            sa.delete(EntityRef).where(EntityRef.id.in_([ref_id for ref_id, *_ in matches]))
        )
        total += len(matches)


async def merge_duplicates(session: AsyncSession) -> int:
    """Exact cross-source duplicates (same content_hash) collapse into one node.

    Winner: higher source authority, then fresher source_updated_at, then the
    older row. Edges transfer with dedup, the loser gets a duplicate_of edge
    and is soft-deleted (its deleted_at marks the merge moment). Fuzzy
    resolution is v2 (lifecycle.html#entity-resolution). The caller holds the
    destructive gate. Returns the number of merged (hidden) duplicates.
    """
    groups = (
        await session.execute(
            sa.select(Entity.content_hash)
            .where(Entity.is_deleted.is_(False), Entity.content_hash.is_not(None))
            .group_by(Entity.content_hash)
            .having(
                sa.func.count() > 1,
                sa.func.count(sa.distinct(Entity.source_id)) > 1,  # cross-source only
            )
        )
    ).scalars()
    authority_rank = sa.case(
        {
            str(AuthorityTier.HIGH): 3,
            str(AuthorityTier.NORMAL): 2,
            str(AuthorityTier.LOW): 1,
        },
        value=sa.func.coalesce(Source.authority_tier, str(AuthorityTier.NORMAL)),
        else_=2,
    )
    merged = 0
    for content_hash in list(groups):
        ids = list(
            await session.scalars(
                sa.select(Entity.id)
                .join(Source, Source.id == Entity.source_id)
                .where(Entity.content_hash == content_hash, Entity.is_deleted.is_(False))
                .order_by(
                    authority_rank.desc(),
                    Entity.source_updated_at.desc().nulls_last(),
                    Entity.id,
                )
            )
        )
        if len(ids) < 2:
            continue
        winner, losers = ids[0], ids[1:]
        for loser in losers:
            await _transfer_edges(session, loser=loser, winner=winner)
            await session.execute(
                pg_insert(EntityEdge)
                .values(
                    src_entity_id=loser,
                    dst_entity_id=winner,
                    rel_type=str(RelType.DUPLICATE_OF),
                    origin=str(EdgeOrigin.CURATION),
                )
                .on_conflict_do_nothing()
            )
            await soft_delete(session, loser)
            merged += 1
    return merged


async def _transfer_edges(session: AsyncSession, *, loser: int, winner: int) -> None:
    """Re-point the loser's edges to the winner, deduped by the triple UNIQUE."""
    outgoing = sa.select(
        sa.literal(winner).label("src_entity_id"),
        EntityEdge.dst_entity_id,
        EntityEdge.rel_type,
        EntityEdge.origin,
    ).where(EntityEdge.src_entity_id == loser, EntityEdge.dst_entity_id != winner)
    await session.execute(
        pg_insert(EntityEdge)
        .from_select(["src_entity_id", "dst_entity_id", "rel_type", "origin"], outgoing)
        .on_conflict_do_nothing()
    )
    incoming = sa.select(
        EntityEdge.src_entity_id,
        sa.literal(winner).label("dst_entity_id"),
        EntityEdge.rel_type,
        EntityEdge.origin,
    ).where(EntityEdge.dst_entity_id == loser, EntityEdge.src_entity_id != winner)
    await session.execute(
        pg_insert(EntityEdge)
        .from_select(["src_entity_id", "dst_entity_id", "rel_type", "origin"], incoming)
        .on_conflict_do_nothing()
    )
    await session.execute(
        sa.delete(EntityEdge).where(
            sa.or_(EntityEdge.src_entity_id == loser, EntityEdge.dst_entity_id == loser)
        )
    )


async def trust_decay(session: AsyncSession) -> int:
    """entities.trust_score = authority x freshness x demand — one set-based UPDATE.

    Absolute recompute (idempotent), never a deletion (lifecycle.html#staleness).
    freshness halves every TRUST_HALF_LIFE_DAYS of source_updated_at age;
    demand grows logarithmically with access_counter hits.
    """
    result = await session.execute(
        sa.text(
            """
            UPDATE entities e
            SET trust_score = sub.score
            FROM (
                SELECT
                    e2.id,
                    (CASE coalesce(s.authority_tier, 'normal')
                        WHEN 'high' THEN CAST(:high AS float8)
                        WHEN 'low' THEN CAST(:low AS float8)
                        ELSE CAST(:normal AS float8) END)
                    * exp(-CAST(:ln2 AS float8) * greatest(
                        extract(epoch FROM (now() - coalesce(e2.source_updated_at, e2.created_at)))
                        / 86400.0, 0) / CAST(:half_life AS float8))
                    * (1 + CAST(:demand_weight AS float8) * ln(1 + coalesce(ac.hits, 0))) AS score
                FROM entities e2
                JOIN sources s ON s.id = e2.source_id
                LEFT JOIN access_counter ac ON ac.entity_ref = e2.id
                WHERE NOT e2.is_deleted
            ) AS sub
            WHERE e.id = sub.id
            """
        ),
        {
            "low": AUTHORITY_WEIGHT[AuthorityTier.LOW],
            "normal": AUTHORITY_WEIGHT[AuthorityTier.NORMAL],
            "high": AUTHORITY_WEIGHT[AuthorityTier.HIGH],
            "ln2": math.log(2),
            "half_life": TRUST_HALF_LIFE_DAYS,
            "demand_weight": DEMAND_LOG_WEIGHT,
        },
    )
    return getattr(result, "rowcount", 0) or 0


async def reembed_batches(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = REEMBED_BATCH,
    retry_wait: float = REEMBED_RUNTIME_WAIT_SECONDS,
    max_retries: int = REEMBED_RUNTIME_MAX_RETRIES,
    loading_poll: float = REEMBED_LOADING_POLL_SECONDS,
    loading_max: float = REEMBED_LOADING_MAX_SECONDS,
    notify: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Row-by-row embedding refresh (lifecycle.html#embedding-refresh).

    Predicate — embedding_model differs from the assigned one or is NULL —
    is idempotent and self-healing: a cancelled refresh resumes on the next
    pass. Each batch commits on its own.

    A failed batch is diagnosed by the runtime's own state, not by elapsed
    time: `loading` waits on its own generous budget (a first-time weights
    download is legitimately long), `error` fails the run at once with the
    runtime's message, silence/`not_loaded` re-kicks the load and burns the
    stall budget. A runtime hopeless past its budget raises
    EmbeddingRuntimeUnavailableError so the run terminates as failed, not a
    hollow success. Returns chunks updated.
    """
    total = 0
    stalls = 0
    loading_waited = 0.0
    while True:
        async with session_factory() as session:
            assigned = await embeddings_client.resolve_assigned(session)
            if assigned is None:
                return total  # nothing assigned — nothing to refresh against
            model, provider = assigned
            rows = (
                await session.execute(
                    sa.select(Chunk.id, Chunk.text, Chunk.token_count)
                    .where(
                        Chunk.is_deleted.is_(False),
                        sa.or_(
                            Chunk.embedding.is_(None),
                            Chunk.embedding_model.is_distinct_from(model.model_id),
                        ),
                    )
                    .order_by(Chunk.id)
                    .limit(batch_size)
                )
            ).all()
            if not rows:
                return total
            result = await embeddings_client.embed(
                session,
                [text for _, text, _ in rows],
                http_timeout=EMBED_BATCH_TIMEOUT_SECONDS,
            )
            if result is None:
                state = None
                if provider.is_system and provider.base_url:
                    status = await embeddings_client.runtime_status(provider.base_url)
                    if status is not None:
                        state = status.state_of(model.model_id)
                        if state == "error":
                            # The runtime tried and failed — waiting cannot help.
                            raise EmbeddingRuntimeUnavailableError(
                                f"embeddings runtime failed to load {model.model_id}: "
                                f"{status.error_of(model.model_id) or 'unknown error'}"
                            )
                if state == "loading":
                    # Observable progress — wait it out on its own budget
                    # without burning the stall retries.
                    loading_waited += loading_poll
                    if loading_waited > loading_max:
                        raise EmbeddingRuntimeUnavailableError(
                            f"embeddings runtime still loading {model.model_id} "
                            f"after {int(loading_max)}s"
                        )
                    logger.info(
                        "re-embed: %s is loading — waited %ss of %ss",
                        model.model_id,
                        int(loading_waited),
                        int(loading_max),
                    )
                    await asyncio.sleep(loading_poll)
                    continue
                stalls += 1
                if stalls > max_retries:
                    raise EmbeddingRuntimeUnavailableError(
                        f"embeddings runtime unanswered after {total} chunks "
                        f"and {max_retries} retries"
                    )
                # (Re-)kick the load: the one-shot warm at assignment time is
                # best-effort and a runtime restart drops the model from memory,
                # so the assigned model may be unloaded with nothing to reload it.
                # /admin/load is idempotent — a no-op while it is loading/ready.
                await embeddings_client.warm_assigned(session, model)
                logger.info(
                    "re-embed: runtime not ready after %s chunks — retry %s/%s in %ss",
                    total,
                    stalls,
                    max_retries,
                    retry_wait,
                )
                await asyncio.sleep(retry_wait)
                continue
            stalls = 0  # progress refills the retry budget
            loading_waited = 0.0
            await session.execute(
                sa.update(Chunk),
                [
                    {
                        "id": chunk_id,
                        "embedding": vector,
                        "embedding_model": result.model.model_id,
                    }
                    for (chunk_id, _, _), vector in zip(rows, result.vectors, strict=True)
                ],
            )
            input_tokens = result.prompt_tokens or sum(
                count or approx_counter(text) for _, text, count in rows
            )
            await record_usage(
                session,
                model_pk=result.model.id,
                function=AiFunction.HARVESTER_EMBEDDING,
                input_tokens=input_tokens,
            )
            await session.commit()
            total += len(rows)
            # Fires after the committed batch so open boards see progress live.
            if notify is not None:
                await notify()
