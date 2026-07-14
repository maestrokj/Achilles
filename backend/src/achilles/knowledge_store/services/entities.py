"""Entity lifecycle: upsert of all projections in one transaction, soft-delete, restore.

data-model.html#consistency — writes land body → chunks → edges in a fixed order
within one transaction; `upsert_entity` is the Loader contract Harvester (stage 5)
will call for every captured record.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.services import tokenizer
from achilles.knowledge_store.constants import EdgeOrigin
from achilles.knowledge_store.repositories import acl as acl_repo
from achilles.knowledge_store.repositories import chunks as chunks_repo
from achilles.knowledge_store.repositories import edges as edges_repo
from achilles.knowledge_store.repositories import entities as entities_repo
from achilles.knowledge_store.services import chunking, embedding_write


@dataclass(frozen=True, slots=True)
class EdgeDraft:
    dst_entity_id: int
    rel_type: str
    weight: float | None = None
    origin: str = EdgeOrigin.HARVESTER


@dataclass(frozen=True, slots=True)
class RefDraft:
    relation: str  # source terms: mentions · blocks · parent · author · duplicate
    target_kind: str  # issue · page · user · commit · mr · message
    target_ref: str  # native target id, resolve key
    source_hint: str | None = None


@dataclass(frozen=True, slots=True)
class AclDraft:
    scope: str
    source_group_id: int | None = None
    source_principal_id: int | None = None


@dataclass(frozen=True, slots=True)
class EntityPayload:
    source_id: int
    source_type: str
    source_entity_id: str
    title: str | None = None
    body: str | None = None
    url: str | None = None
    status: str | None = None
    author_principal_id: int | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    meta: dict[str, Any] | None = None
    edges: tuple[EdgeDraft, ...] = field(default=())
    refs: tuple[RefDraft, ...] = field(default=())
    acl: tuple[AclDraft, ...] = field(default=())


def _content_hash(payload: EntityPayload) -> str:
    material = f"{payload.title or ''}\x00{payload.body or ''}"
    return hashlib.sha256(material.encode()).hexdigest()


async def upsert_entity(
    session: AsyncSession,
    payload: EntityPayload,
    *,
    token_counter: tokenizer.TokenCounter | None = None,
    embed_inline: bool = True,
) -> int:
    """Idempotent upsert of one entity with all projections; returns the entity id.

    Order is fixed: body → chunks → edges/refs → ACL. Re-capture revives a
    soft-deleted entity; unchanged fragments are not touched (content_hash diff).

    A batch caller (Harvester, stage 5) resolves the token counter once per run
    and passes it in; left None it is resolved per call, which costs an extra
    assignment lookup per entity — fine for one-off writes, not for bulk ingest.
    The same batch caller passes embed_inline=False and embeds the whole page in
    one call at its page boundary — pending chunks are simply embedding IS NULL,
    so deferral changes nothing but the batch size.
    """
    entity_id = await entities_repo.upsert_row(
        session,
        {
            "source_id": payload.source_id,
            "source_type": payload.source_type,
            "source_entity_id": payload.source_entity_id,
            "title": payload.title,
            "body": payload.body,
            "url": payload.url,
            "status": payload.status,
            "author_principal_id": payload.author_principal_id,
            "source_created_at": payload.source_created_at,
            "source_updated_at": payload.source_updated_at,
            "content_hash": _content_hash(payload),
            "meta": payload.meta,
        },
    )
    if token_counter is None:
        token_counter = await tokenizer.get_token_counter(session)
    await chunks_repo.apply_diff(
        session, entity_id, chunking.chunk_body(payload.body, token_counter=token_counter)
    )
    await edges_repo.upsert_edges(session, entity_id, payload.edges)
    await edges_repo.stage_refs(session, entity_id, payload.refs)
    await acl_repo.replace_grants(session, entity_id, payload.acl)
    if embed_inline:
        await embedding_write.embed_pending(session, [entity_id])
    return entity_id


async def soft_delete(
    session: AsyncSession, entity_id: int, *, deleted_at: datetime | None = None
) -> None:
    """Hide the entity from every primitive; a separate axis from status (test_soft_delete)."""
    await entities_repo.set_deleted(session, entity_id, deleted=True, deleted_at=deleted_at)


async def restore(session: AsyncSession, entity_id: int) -> None:
    await entities_repo.set_deleted(session, entity_id, deleted=False, deleted_at=None)


async def count_entities_for_source(session: AsyncSession, source_id: int) -> int:
    """Live entity contribution of one source — the Harvester hub "Entities" column."""
    return await entities_repo.count_for_source(session, source_id)


async def entity_counts_by_source(session: AsyncSession) -> dict[int, int]:
    """Live entity count per source in one query — the Harvester hub list (avoids N+1)."""
    counts = await entities_repo.counts_by_source(session)
    return {source_id: entities for source_id, (entities, _) in counts.items()}
