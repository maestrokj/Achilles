"""Graph-projection statements: edge upsert, unresolved-ref staging (data-model.html#graph)."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import EntityEdge, EntityRef

if TYPE_CHECKING:
    from achilles.knowledge_store.services.entities import EdgeDraft, RefDraft


async def upsert_edges(
    session: AsyncSession, src_entity_id: int, edges: Iterable[EdgeDraft]
) -> None:
    """Idempotent edge upsert by (src, dst, rel_type); re-capture refreshes the weight."""
    rows = [
        {
            "src_entity_id": src_entity_id,
            "dst_entity_id": e.dst_entity_id,
            "rel_type": e.rel_type,
            "weight": e.weight,
            "origin": e.origin,
        }
        for e in edges
    ]
    if not rows:
        return
    stmt = pg_insert(EntityEdge).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_entity_edge_triple", set_={"weight": stmt.excluded.weight}
        )
    )


async def stage_refs(session: AsyncSession, src_entity_id: int, refs: Iterable[RefDraft]) -> None:
    """Stage unresolved links; ON CONFLICT DO NOTHING on the natural key (re-capture is a no-op)."""
    rows = [
        {
            "src_entity_id": src_entity_id,
            "relation": r.relation,
            "target_kind": r.target_kind,
            "target_ref": r.target_ref,
            "source_hint": r.source_hint,
        }
        for r in refs
    ]
    if not rows:
        return
    await session.execute(
        pg_insert(EntityRef).values(rows).on_conflict_do_nothing(constraint="uq_entity_ref_natural")
    )
