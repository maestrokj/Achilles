"""Recursive-CTE builder for graph traversal (data-model.html#graph).

Contract: depth 1..3 · direction src→dst filtered by rel_type · cycle guard via
the visited path array · width bounds (fanout cap + weight threshold) fire
INSIDE the lateral hop, before the ACL JOIN — a hub node must not expand into
thousands of edges only to be filtered later. The ACL clause sits inside the
recursive member: a denied node breaks the path, it is not post-filtered.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import aliased

from achilles.knowledge_store.constants import (
    GRAPH_DEPTH_MAX,
    GRAPH_DEPTH_MIN,
    GRAPH_FANOUT_CAP,
)
from achilles.knowledge_store.models import Entity, EntityEdge
from achilles.knowledge_store.retrieval.acl import acl_prefilter, source_scope


def build_traversal(
    *,
    start_ids: Sequence[int],
    user_id: int,
    depth: int,
    rel_types: Sequence[str] | None = None,
    weight_min: float | None = None,
    allowed_source_ids: Sequence[int] | None = None,
) -> sa.Select[tuple[int, int]]:
    """Build the walk: (entity_id, min depth) per reachable node, nearest first, seeds excluded."""
    if not GRAPH_DEPTH_MIN <= depth <= GRAPH_DEPTH_MAX:
        msg = f"traversal depth must be within {GRAPH_DEPTH_MIN}..{GRAPH_DEPTH_MAX}, got {depth}"
        raise ValueError(msg)

    seed_where = [
        Entity.id.in_(start_ids),
        sa.not_(Entity.is_deleted),
        acl_prefilter(Entity.id, user_id),
        # API-key scope shares the ACL slot: an out-of-scope node breaks the path.
        *source_scope(Entity.source_id, allowed_source_ids),
    ]
    seed = sa.select(
        Entity.id.label("entity_id"),
        sa.literal(0).label("depth"),
        array([Entity.id]).label("path"),
    ).where(*seed_where)
    walk = seed.cte("walk", recursive=True)

    # Width bounds — everything in this lateral runs before the ACL JOIN.
    hop_where = [
        EntityEdge.src_entity_id == walk.c.entity_id,
        sa.not_(EntityEdge.dst_entity_id == sa.any_(walk.c.path)),  # cycle guard
    ]
    if rel_types:
        hop_where.append(EntityEdge.rel_type.in_(rel_types))
    if weight_min is not None:
        hop_where.append(sa.func.coalesce(EntityEdge.weight, 1.0) >= weight_min)
    hop = (
        sa.select(EntityEdge.dst_entity_id.label("dst"))
        .where(*hop_where)
        .order_by(EntityEdge.weight.desc().nulls_last(), EntityEdge.dst_entity_id)
        .limit(GRAPH_FANOUT_CAP)
        .lateral("hop")
    )

    dst = aliased(Entity)
    step_where = [
        walk.c.depth < depth,
        sa.not_(dst.is_deleted),
        acl_prefilter(dst.id, user_id),  # the ACL slot: a denied node breaks the path
        *source_scope(dst.source_id, allowed_source_ids),
    ]
    step = (
        sa.select(
            hop.c.dst.label("entity_id"),
            (walk.c.depth + 1).label("depth"),
            walk.c.path.op("||")(hop.c.dst).label("path"),
        )
        .select_from(walk.join(hop, sa.true()).join(dst, dst.id == hop.c.dst))
        .where(*step_where)
    )
    walk = walk.union_all(step)

    # Ranking lives in the query so the caller's LIMIT keeps top_k rows, not
    # the whole reachable set.
    min_depth = sa.func.min(walk.c.depth).label("depth")
    return (
        sa.select(walk.c.entity_id, min_depth)
        .where(walk.c.depth > 0)
        .group_by(walk.c.entity_id)
        .order_by(min_depth, walk.c.entity_id)
    )
