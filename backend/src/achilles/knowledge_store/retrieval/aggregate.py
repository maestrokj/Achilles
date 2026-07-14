"""Aggregate primitive: counts over the relational body — the engine does the math.

The agent sql tool's second mode (hybrid-search.html#primitives): a closed
list of group-by axes under the same ACL pre-filter as every read. Bound
values only, no free-form SQL; KS hands over numbers, never prose.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import Entity
from achilles.knowledge_store.retrieval.acl import acl_prefilter
from achilles.knowledge_store.retrieval.sql import SqlFilters, apply_filters

MAX_GROUPS = 50

# Closed axis list; a month axis buckets by the source-side timestamp.
# Any covers the InstrumentedAttribute / function-expression split that
# ColumnElement can't; the expressions are immutable and safely reusable.
GROUP_BY_AXES: dict[str, Any] = {
    "source_type": Entity.source_type,
    "status": sa.func.coalesce(Entity.status, "(none)"),
    "created_month": sa.func.to_char(Entity.source_created_at, "YYYY-MM"),
    "updated_month": sa.func.to_char(Entity.source_updated_at, "YYYY-MM"),
}


async def aggregate(
    session: AsyncSession,
    *,
    user_id: int,
    group_by: str,
    filters: SqlFilters | None = None,
    limit: int = MAX_GROUPS,
) -> list[tuple[str, int]]:
    """(group, count) rows, biggest first; unknown axis → ValueError for the caller."""
    axis = GROUP_BY_AXES.get(group_by)
    if axis is None:
        msg = f"unknown group_by {group_by!r}; expected one of {sorted(GROUP_BY_AXES)}"
        raise ValueError(msg)
    stmt = (
        sa.select(axis.label("bucket"), sa.func.count().label("total"))
        .select_from(Entity)
        .where(sa.not_(Entity.is_deleted), acl_prefilter(Entity.id, user_id))
    )
    if filters is not None:
        stmt = apply_filters(stmt, filters)
    stmt = stmt.group_by(axis).order_by(sa.func.count().desc(), axis).limit(min(limit, MAX_GROUPS))
    rows = await session.execute(stmt)
    return [(str(bucket) if bucket is not None else "(none)", int(total)) for bucket, total in rows]
