"""Graph primitive: executes the traversal builder; score = 1/(1+depth)."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.constants import MAX_TOP_K
from achilles.knowledge_store.retrieval.hits import Hit
from achilles.knowledge_store.retrieval.traversal import build_traversal


async def search(
    session: AsyncSession,
    *,
    user_id: int,
    start_ids: Sequence[int],
    depth: int,
    rel_types: Sequence[str] | None = None,
    weight_min: float | None = None,
    top_k: int,
    allowed_source_ids: Sequence[int] | None = None,
) -> list[Hit]:
    stmt = build_traversal(
        start_ids=start_ids,
        user_id=user_id,
        depth=depth,
        rel_types=rel_types,
        weight_min=weight_min,
        allowed_source_ids=allowed_source_ids,
    ).limit(min(top_k, MAX_TOP_K))
    rows = await session.execute(stmt)
    return [
        Hit(entity_id=entity_id, score=1.0 / (1.0 + found_depth), depth=found_depth)
        for entity_id, found_depth in rows
    ]
