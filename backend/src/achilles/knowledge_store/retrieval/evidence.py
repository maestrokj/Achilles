"""Evidence for fused hits: record-level fields + the best fragment's text.

The consumer (Query Engine augment) packs these into the model context and
builds record-level citations — KS hands over data, never prose.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import Chunk, Entity
from achilles.knowledge_store.retrieval.fusion import FusedHit


@dataclass(frozen=True, slots=True)
class Evidence:
    entity_id: int
    title: str | None
    url: str | None
    source_type: str
    best_chunk_id: int | None
    best_chunk_text: str | None


async def fetch_evidence(session: AsyncSession, hits: Sequence[FusedHit]) -> list[Evidence]:
    """Hydrate hits in their fused order; unknown ids are silently dropped."""
    if not hits:
        return []
    entity_rows = {
        row.id: row
        for row in (
            await session.execute(
                sa.select(Entity.id, Entity.title, Entity.url, Entity.source_type).where(
                    Entity.id.in_([hit.entity_id for hit in hits])
                )
            )
        ).all()
    }
    chunk_ids = [hit.best_chunk_id for hit in hits if hit.best_chunk_id is not None]
    chunk_texts: dict[int, str] = {}
    if chunk_ids:
        rows = await session.execute(sa.select(Chunk.id, Chunk.text).where(Chunk.id.in_(chunk_ids)))
        chunk_texts = dict(rows.tuples().all())
    evidence: list[Evidence] = []
    for hit in hits:
        entity = entity_rows.get(hit.entity_id)
        if entity is None:
            continue
        evidence.append(
            Evidence(
                entity_id=hit.entity_id,
                title=entity.title,
                url=entity.url,
                source_type=entity.source_type,
                best_chunk_id=hit.best_chunk_id,
                best_chunk_text=(
                    chunk_texts.get(hit.best_chunk_id) if hit.best_chunk_id is not None else None
                ),
            )
        )
    return evidence
