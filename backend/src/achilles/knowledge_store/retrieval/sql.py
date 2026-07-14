"""SQL primitive: exact filters over the relational body — a closed field list.

hybrid-search.html#primitives — bound values only, no free-form SQL; recency
ordering stands in for relevance (score = 1/(1+rank)). The aggregate tool mode
is a separate Agent Engine concern (stages 4/6).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.constants import MAX_TOP_K
from achilles.knowledge_store.models import Entity
from achilles.knowledge_store.retrieval.acl import acl_prefilter, source_scope
from achilles.knowledge_store.retrieval.hits import Hit


@dataclass(frozen=True, slots=True)
class SqlFilters:
    source_ids: Sequence[int] | None = None
    source_types: Sequence[str] | None = None
    statuses: Sequence[str] | None = None
    source_created_from: datetime | None = None
    source_created_to: datetime | None = None
    source_updated_from: datetime | None = None
    source_updated_to: datetime | None = None


# The tool-facing wire subset of SqlFilters (chat search_knowledge + agent core).
FILTERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_types": {"type": "array", "items": {"type": "string"}},
        "statuses": {"type": "array", "items": {"type": "string"}},
        "updated_from": {"type": "string", "format": "date-time"},
        "updated_to": {"type": "string", "format": "date-time"},
    },
    "additionalProperties": False,
}


def parse_filters(raw: object) -> SqlFilters | None:
    """Model-supplied FILTERS_JSON_SCHEMA payload → SqlFilters; all-empty → None."""
    if not isinstance(raw, dict):
        return None

    def _texts(key: str) -> list[str] | None:
        value = raw.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
        return None

    def _moment(key: str) -> datetime | None:
        value = raw.get(key)
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    filters = SqlFilters(
        source_types=_texts("source_types"),
        statuses=_texts("statuses"),
        source_updated_from=_moment("updated_from"),
        source_updated_to=_moment("updated_to"),
    )
    if not any(
        (
            filters.source_types,
            filters.statuses,
            filters.source_updated_from,
            filters.source_updated_to,
        )
    ):
        return None
    return filters


def apply_filters[SelectT: sa.Select[Any]](stmt: SelectT, filters: SqlFilters) -> SelectT:
    """Shared by the search primitive and the aggregate mode (Agent Engine)."""
    if filters.source_ids:
        stmt = stmt.where(Entity.source_id.in_(filters.source_ids))
    if filters.source_types:
        stmt = stmt.where(Entity.source_type.in_(filters.source_types))
    if filters.statuses:
        stmt = stmt.where(Entity.status.in_(filters.statuses))
    if filters.source_created_from:
        stmt = stmt.where(Entity.source_created_at >= filters.source_created_from)
    if filters.source_created_to:
        stmt = stmt.where(Entity.source_created_at <= filters.source_created_to)
    if filters.source_updated_from:
        stmt = stmt.where(Entity.source_updated_at >= filters.source_updated_from)
    if filters.source_updated_to:
        stmt = stmt.where(Entity.source_updated_at <= filters.source_updated_to)
    return stmt


async def search(
    session: AsyncSession,
    *,
    user_id: int,
    filters: SqlFilters,
    top_k: int,
    allowed_source_ids: Sequence[int] | None = None,
) -> list[Hit]:
    stmt = apply_filters(
        sa.select(Entity.id).where(
            sa.not_(Entity.is_deleted),
            acl_prefilter(Entity.id, user_id),
            *source_scope(Entity.source_id, allowed_source_ids),
        ),
        filters,
    )
    stmt = stmt.order_by(Entity.source_updated_at.desc().nulls_last(), Entity.id).limit(
        min(top_k, MAX_TOP_K)
    )
    rows = await session.execute(stmt)
    return [
        Hit(entity_id=entity_id, score=1.0 / (1.0 + rank)) for rank, (entity_id,) in enumerate(rows)
    ]
