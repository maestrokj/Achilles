"""Embed-on-write: best-effort vectors for chunks that lack them.

apply_diff has already NULLed the embedding of every changed fragment, so
"pending" is simply embedding IS NULL. One-off writers call this at the tail
of upsert_entity; the Harvester sync defers per-item embedding and calls it
once per page with the whole page's entities — a CPU encoder does far better
on one large batch than on fifty two-text calls. A silent runtime leaves rows
NULL and ingest succeeds; searches skip them. The bulk counterpart (model
change) is curation_steps.reembed_batches.
"""

from collections.abc import Collection
from itertools import batched

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.services import embeddings_client
from achilles.ai_foundation.services.tokenizer import approx_counter
from achilles.ai_foundation.services.usage import record_usage
from achilles.knowledge_store.constants import EMBED_BATCH_TIMEOUT_SECONDS, EMBED_WRITE_BATCH
from achilles.knowledge_store.models import Chunk


async def embed_pending(session: AsyncSession, entity_ids: Collection[int]) -> None:
    """Fill missing vectors of the given entities' live chunks; never raises.

    Runs in EMBED_WRITE_BATCH slices so a large page stays within the HTTP
    budget; a slice the runtime doesn't answer stops the loop — the remaining
    rows stay NULL until a re-upsert or the next model-change re-embed.
    """
    if not entity_ids:
        return
    rows = (
        await session.execute(
            sa.select(Chunk.id, Chunk.text, Chunk.token_count)
            .where(
                Chunk.entity_id.in_(entity_ids),
                sa.not_(Chunk.is_deleted),
                Chunk.embedding.is_(None),
            )
            .order_by(Chunk.entity_id, Chunk.ordinal)
        )
    ).all()
    for batch in batched(rows, EMBED_WRITE_BATCH, strict=False):
        result = await embeddings_client.embed(
            session, [text for _, text, _ in batch], http_timeout=EMBED_BATCH_TIMEOUT_SECONDS
        )
        if result is None:
            return
        await session.execute(
            sa.update(Chunk),
            [
                {"id": chunk_id, "embedding": vector, "embedding_model": result.model.model_id}
                for (chunk_id, _, _), vector in zip(batch, result.vectors, strict=True)
            ],
        )
        input_tokens = result.prompt_tokens or sum(
            count or approx_counter(text) for _, text, count in batch
        )
        await record_usage(
            session,
            model_pk=result.model.id,
            function=AiFunction.HARVESTER_EMBEDDING,
            input_tokens=input_tokens,
        )
