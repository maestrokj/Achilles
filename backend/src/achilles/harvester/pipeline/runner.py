"""Sync run executor: fetch → normalize → stations → Load (pipeline.html).

One code path for every mode — the mode only picks the `since` window and
whether deletions/ACL are reconciled. Item failures land in dead_letters and
the run keeps going (partial success); page boundaries checkpoint progress
and notice a cancel. Item writes run inside savepoints so one poisoned item
never rolls back its page.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.ai_foundation.services import tokenizer
from achilles.harvester.connectors.base import (
    BaseConnector,
    NormalizedEntity,
    PrincipalDraft,
    RawItem,
)
from achilles.harvester.connectors.http import SourceItemError, SourceUnavailableError
from achilles.harvester.constants import DlqReason, SyncMode, SyncState
from achilles.harvester.pipeline import stations
from achilles.harvester.services import dead_letters, principals, sync_runs
from achilles.knowledge_store.constants import REL_TYPE_BY_RELATION, AclScope, RelType
from achilles.knowledge_store.models import Chunk, Entity, SourceGroup
from achilles.knowledge_store.services import embedding_write
from achilles.knowledge_store.services.entities import (
    AclDraft,
    EdgeDraft,
    EntityPayload,
    RefDraft,
    upsert_entity,
)

logger = logging.getLogger(__name__)

# Commit + progress + cancel-check cadence, in items.
CHECKPOINT_EVERY = 50
# Ids per soft-delete UPDATE during reconciliation (bounds statement size).
SOFT_DELETE_BATCH = 500


@dataclass(slots=True)
class SyncOutcome:
    state: str
    done: int = 0
    errors: int = 0
    error_detail: str | None = None
    watermark: str | None = None  # max(source_updated_at) seen — the next cursor


@dataclass(slots=True)
class _RunContext:
    connector: BaseConnector
    run_id: int
    source_id: int
    token_counter: tokenizer.TokenCounter | None
    principal_pk: dict[str, int] = field(default_factory=dict)
    group_pk: dict[str, int] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    dlq_keys: set[tuple[str, str]] = field(default_factory=set)  # open (type, native id)
    done: int = 0
    errors: int = 0
    since_checkpoint: int = 0  # items consumed since the last page boundary
    pending_embed: set[int] = field(default_factory=set)  # entity ids of the current page
    watermark: datetime | None = None
    # Fires after each committed page so open boards see progress live.
    notify: Callable[[], Awaitable[None]] | None = None


async def execute_run(
    session_factory: async_sessionmaker[AsyncSession],
    connector: BaseConnector,
    *,
    run_id: int,
    source_id: int,
    mode: str,
    scope: dict[str, object] | None,
    since: datetime | None,
    initial_done: int = 0,
    notify: Callable[[], Awaitable[None]] | None = None,
) -> SyncOutcome:
    """Run the shared pipeline; the caller owns the journal transitions around it."""
    async with session_factory() as session:
        open_dlq = await dead_letters.items_for_source(session, source_id)
        ctx = _RunContext(
            connector=connector,
            run_id=run_id,
            source_id=source_id,
            token_counter=await tokenizer.get_token_counter(session),
            dlq_keys={(i["source_type"], i["source_entity_id"]) for i in open_dlq},
            done=initial_done,
            notify=notify,
        )
        try:
            if mode in (str(SyncMode.FULL), str(SyncMode.RECONCILIATION)):
                await _ingest_people(session, ctx)
            items = scope.get("items") if scope else None
            if isinstance(items, list):
                await _consume_targeted(session, ctx, items)
            else:
                await _consume_stream(session, ctx, since)
            if mode == str(SyncMode.RECONCILIATION):
                await _reconcile_deletions(session, ctx)
            await session.commit()
        except (SourceUnavailableError, SourceItemError) as exc:
            # SourceItemError normally dead-letters the item (in _process_item);
            # escaping the connector's fetch() generator kills the stream, so
            # here it can only fail the run — a dead generator cannot resume.
            await session.rollback()
            return SyncOutcome(
                state=str(SyncState.FAILED),
                done=ctx.done,
                errors=ctx.errors,
                error_detail=str(exc),
                watermark=_iso(ctx.watermark),
            )
        except _CancelledRunError:
            await session.rollback()
            return SyncOutcome(
                state=str(SyncState.CANCELLED),
                done=ctx.done,
                errors=ctx.errors,
                watermark=_iso(ctx.watermark),
            )
        return SyncOutcome(
            state=str(SyncState.SUCCEEDED),
            done=ctx.done,
            errors=ctx.errors,
            watermark=_iso(ctx.watermark),
        )


class _CancelledRunError(Exception):
    """Internal control flow: the journal row left `running` under our feet."""


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def _ingest_people(session: AsyncSession, ctx: _RunContext) -> None:
    """Full principal/group/membership sweep (full + reconciliation modes).

    Incremental runs skip it — authors of new items are upserted on the way,
    revocations wait for the next reconciliation (eventually consistent by
    design, acl-identity.html#permission-sync).
    """
    async for person in ctx.connector.fetch_principals():
        ctx.principal_pk[person.source_user_id] = await principals.upsert_principal(
            session, source_id=ctx.source_id, draft=person
        )
    async for group in ctx.connector.fetch_groups():
        ctx.group_pk[group.source_group_id] = await principals.ingest_group(
            session,
            source_id=ctx.source_id,
            draft=group,
            principal_pk_by_native=ctx.principal_pk,
        )
    await session.commit()


async def _consume_stream(session: AsyncSession, ctx: _RunContext, since: datetime | None) -> None:
    async for raw in ctx.connector.fetch(since):
        await _process_item(session, ctx, raw)
        ctx.since_checkpoint += 1
        if ctx.since_checkpoint >= CHECKPOINT_EVERY:
            await _page_boundary(session, ctx)
            ctx.since_checkpoint = 0
    await _page_boundary(session, ctx)


async def _consume_targeted(session: AsyncSession, ctx: _RunContext, items: list[object]) -> None:
    """DLQ retry: fetch each item point-blank (sync-modes.html#dlq-retry)."""
    for entry in items:
        if not isinstance(entry, dict):
            continue
        source_type = str(entry.get("source_type", ""))
        source_entity_id = str(entry.get("source_entity_id", ""))
        raw = await ctx.connector.fetch_item(source_type, source_entity_id)
        if raw is None:
            # Targeted fetch unsupported / item gone — reconciliation drains it.
            logger.info(
                "dlq retry: %s %s not refetchable on source %s",
                source_type,
                source_entity_id,
                ctx.source_id,
            )
            continue
        await _process_item(session, ctx, raw)
    await _page_boundary(session, ctx)


async def _page_boundary(session: AsyncSession, ctx: _RunContext) -> None:
    """Notice a cancel/reap, then commit the page and persist progress.

    The check comes first: a cancelled run must not land its current page —
    READ COMMITTED sees the API's terminal write from here.
    """
    state = await sync_runs.get_state(session, ctx.run_id)
    if state != str(SyncState.RUNNING):
        await session.rollback()
        raise _CancelledRunError
    # One embeddings call for the whole page (upserts deferred with
    # embed_inline=False) — same transaction, so visibility is unchanged.
    await embedding_write.embed_pending(session, ctx.pending_embed)
    ctx.pending_embed.clear()
    checkpoint = {
        "watermark": _iso(ctx.watermark),
        "done": ctx.done,
        "saved_at": datetime.now(UTC).isoformat(),
    }
    await sync_runs.update_progress(
        session,
        ctx.run_id,
        entities_done=ctx.done,
        checkpoint=checkpoint,
        error_count=ctx.errors,
    )
    await session.commit()
    if ctx.notify is not None:
        await ctx.notify()


async def _process_item(session: AsyncSession, ctx: _RunContext, raw: RawItem) -> None:
    try:
        async with session.begin_nested():
            normalized = ctx.connector.normalize(raw)
            if not stations.keep(normalized):
                return
            payload = await _build_payload(session, ctx, normalized)
            entity_id = await upsert_entity(
                session, payload, token_counter=ctx.token_counter, embed_inline=False
            )
        ctx.pending_embed.add(entity_id)
        key = (raw.source_type, raw.source_entity_id)
        if key in ctx.dlq_keys:  # the DLQ is almost always empty — skip the DELETE
            await dead_letters.resolve(
                session,
                source_id=ctx.source_id,
                source_type=raw.source_type,
                source_entity_id=raw.source_entity_id,
            )
            ctx.dlq_keys.discard(key)
        ctx.seen.add(raw.source_entity_id)
        ctx.done += 1
        if normalized.source_updated_at and (
            ctx.watermark is None or normalized.source_updated_at > ctx.watermark
        ):
            ctx.watermark = normalized.source_updated_at
    except SourceItemError as exc:
        await _to_dlq(session, ctx, raw, str(exc.reason), exc.detail)
    except SourceUnavailableError:
        raise  # the whole source is down — fail the run, not the item
    except Exception:
        logger.exception(
            "item %s/%s failed on source %s",
            raw.source_type,
            raw.source_entity_id,
            ctx.source_id,
        )
        await _to_dlq(session, ctx, raw, str(DlqReason.UNKNOWN), "unhandled processing error")


async def _to_dlq(
    session: AsyncSession, ctx: _RunContext, raw: RawItem, reason: str, detail: str | None
) -> None:
    await dead_letters.record(
        session,
        source_id=ctx.source_id,
        run_id=ctx.run_id,
        source_type=raw.source_type,
        source_entity_id=raw.source_entity_id,
        reason=reason,
        error_detail=detail,
    )
    ctx.dlq_keys.add((raw.source_type, raw.source_entity_id))
    ctx.errors += 1


async def _build_payload(
    session: AsyncSession, ctx: _RunContext, normalized: NormalizedEntity
) -> EntityPayload:
    author_pk = None
    if normalized.author is not None:
        author_pk = await _principal_pk(session, ctx, normalized.author)

    acl: list[AclDraft] = []
    for grant in normalized.acl:
        if grant.scope == AclScope.PUBLIC:
            acl.append(AclDraft(scope=str(AclScope.PUBLIC)))
        elif grant.scope == AclScope.GROUP and grant.native_id:
            acl.append(
                AclDraft(
                    scope=str(AclScope.GROUP),
                    source_group_id=await _group_pk(session, ctx, grant.native_id),
                )
            )
        elif grant.scope == AclScope.PRINCIPAL and grant.native_id:
            pk = ctx.principal_pk.get(grant.native_id)
            if pk is None:
                pk = await _principal_pk(
                    session, ctx, PrincipalDraft(source_user_id=grant.native_id)
                )
            acl.append(AclDraft(scope=str(AclScope.PRINCIPAL), source_principal_id=pk))

    edges: list[EdgeDraft] = []
    refs: list[RefDraft] = []
    dst_by_ref: dict[str, int] = {}
    if normalized.links:
        rows = await session.execute(
            sa.select(Entity.source_entity_id, Entity.id).where(
                Entity.source_id == ctx.source_id,
                Entity.source_entity_id.in_({link.target_ref for link in normalized.links}),
            )
        )
        dst_by_ref = dict(rows.tuples().all())
    for link in normalized.links:
        dst = dst_by_ref.get(link.target_ref)
        if dst is not None:
            edges.append(
                EdgeDraft(
                    dst_entity_id=dst,
                    rel_type=str(REL_TYPE_BY_RELATION.get(link.relation, RelType.LINKS_TO)),
                )
            )
        else:
            refs.append(
                RefDraft(
                    relation=link.relation,
                    target_kind=link.target_kind,
                    target_ref=link.target_ref,
                    source_hint=ctx.connector.manifest.type,
                )
            )

    return EntityPayload(
        source_id=ctx.source_id,
        source_type=normalized.source_type,
        source_entity_id=normalized.source_entity_id,
        title=normalized.title,
        body=normalized.body,
        url=normalized.url,
        status=stations.classify(normalized),
        author_principal_id=author_pk,
        source_created_at=normalized.source_created_at,
        source_updated_at=normalized.source_updated_at,
        meta=normalized.meta or None,
        edges=tuple(edges),
        refs=tuple(refs),
        acl=tuple(acl),
    )


async def _principal_pk(session: AsyncSession, ctx: _RunContext, draft: PrincipalDraft) -> int:
    pk = ctx.principal_pk.get(draft.source_user_id)
    if pk is None:
        pk = await principals.upsert_principal(session, source_id=ctx.source_id, draft=draft)
        ctx.principal_pk[draft.source_user_id] = pk
    return pk


async def _group_pk(session: AsyncSession, ctx: _RunContext, native_id: str) -> int:
    """Cache → DB → upsert-on-first-sight (incremental runs skip the group sweep)."""
    pk = ctx.group_pk.get(native_id)
    if pk is not None:
        return pk
    existing = await session.scalar(
        sa.select(SourceGroup.id).where(
            SourceGroup.source_id == ctx.source_id,
            SourceGroup.source_group_id == native_id,
        )
    )
    if existing is None:
        kinds = ctx.connector.manifest.scope_kinds
        existing = await principals.upsert_group(
            session,
            source_id=ctx.source_id,
            source_group_id=native_id,
            name=native_id,
            kind=kinds[0] if kinds else None,
        )
    ctx.group_pk[native_id] = existing
    return existing


async def _reconcile_deletions(session: AsyncSession, ctx: _RunContext) -> None:
    """Only the full reconciliation scan sees disappearances (data-model.html#deletes)."""
    rows = await session.execute(
        sa.select(Entity.id, Entity.source_type, Entity.source_entity_id).where(
            Entity.source_id == ctx.source_id,
            Entity.is_deleted.is_(False),
        )
    )
    # An item that failed this sweep (open DLQ row) was not seen, but it did
    # not vanish from the source — deleting it would turn a fetch error into
    # data loss. Items dropped by the stations filter stay deletable.
    vanished = [
        entity_id
        for entity_id, source_type, native_id in rows
        if native_id not in ctx.seen and (source_type, native_id) not in ctx.dlq_keys
    ]
    vanished_at = datetime.now(UTC)
    # Set-based soft delete: same column effects as entities.soft_delete
    # (flip the body, mirror onto chunks), one statement pair per batch.
    for batch in batched(vanished, SOFT_DELETE_BATCH, strict=False):
        ids = list(batch)
        await session.execute(
            sa.update(Entity)
            .where(Entity.id.in_(ids))
            .values(is_deleted=True, deleted_at=vanished_at)
        )
        await session.execute(
            sa.update(Chunk).where(Chunk.entity_id.in_(ids)).values(is_deleted=True)
        )
