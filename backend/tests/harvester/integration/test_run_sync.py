"""End-to-end sync pipeline on a fake connector: projections, modes, DLQ, resume (P0)."""

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Self, cast

import httpx
import pytest
import respx
import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.config import Settings
from achilles.db.connections import close_connections, create_connections
from achilles.harvester import jobs
from achilles.harvester.connectors.base import (
    AclNative,
    BaseConnector,
    ConnectorManifest,
    Diagnosis,
    GroupDraft,
    LinkDraft,
    NormalizedEntity,
    PrincipalDraft,
    RawItem,
    ScopeObject,
)
from achilles.harvester.connectors.http import SourceItemError, SourceUnavailableError, Throttle
from achilles.harvester.constants import DlqReason, SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import DeadLetter, SyncRun
from achilles.harvester.pipeline import runner
from achilles.harvester.services import sync_runs
from achilles.knowledge_store.constants import AclScope
from achilles.knowledge_store.models import (
    Chunk,
    Entity,
    EntityAcl,
    EntityEdge,
    EntityRef,
    GroupMembership,
    Identity,
    Source,
    SourceGroup,
    SourcePrincipal,
)
from tests.factories.ai import EMBEDDINGS_URL, assign_embedding
from tests.factories.knowledge import create_source

pytestmark = [pytest.mark.integration, pytest.mark.p0]

CTX = cast("Context", {})

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


class FakeDataset:
    """Mutable in-memory source the tests steer between runs."""

    def __init__(self) -> None:
        self.people = [
            PrincipalDraft("u1", "alice@example.com", "Alice"),
            PrincipalDraft("u2", "bob@example.com", "Bob"),
        ]
        self.groups = [GroupDraft("g1", "Team", "project", ("u1", "u2"))]
        self.items: list[dict[str, object]] = [
            {
                "id": "DOC-1",
                "title": "First doc",
                "body": "Body of the first document.",
                "updated": NOW,
                "author": "u1",
                "links": [("mentions", "DOC-2")],
            },
            {
                "id": "DOC-2",
                "title": "Second doc",
                "body": "Body of the second document.",
                "updated": NOW + timedelta(minutes=5),
                "author": "u2",
                "links": [("child_of", "DOC-1")],
            },
        ]
        self.poison: set[str] = set()
        self.last_since: datetime | None | str = "unset"


DATASET = FakeDataset()


class FakeConnector(BaseConnector):
    manifest: ClassVar[ConnectorManifest] = ConnectorManifest(
        type="fake",
        title="Fake",
        needs_base_url=False,
        credential_label="token",
        scope_kinds=("project",),
        rate_limit_per_second=1000.0,
    )

    @classmethod
    def create(
        cls,
        *,
        base_url: str | None,
        credential: str,
        throttle: Throttle | None = None,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> Self:
        del base_url, credential, throttle
        return cls(scope_mode=scope_mode, scope_list=scope_list, content_filters=content_filters)

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        DATASET.last_since = since
        for item in sorted(DATASET.items, key=lambda i: cast("datetime", i["updated"])):
            updated = cast("datetime", item["updated"])
            if since is not None and updated < since:
                continue
            yield RawItem(source_type="doc", source_entity_id=str(item["id"]), payload=item)

    def normalize(self, raw: RawItem) -> NormalizedEntity:
        if raw.source_entity_id in DATASET.poison:
            raise SourceItemError(DlqReason.MALFORMED, "poisoned item")
        item = raw.payload
        author = str(item["author"])
        return NormalizedEntity(
            source_type="doc",
            source_entity_id=raw.source_entity_id,
            title=str(item["title"]),
            body=str(item["body"]),
            status=None,
            author=PrincipalDraft(author),
            source_updated_at=cast("datetime", item["updated"]),
            acl=(AclNative(AclScope.GROUP, "g1"),),
            links=tuple(
                LinkDraft(relation, "doc", target)
                for relation, target in cast("list[tuple[str, str]]", item.get("links", []))
            ),
        )

    async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        for person in DATASET.people:
            yield person

    async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        for group in DATASET.groups:
            yield group

    async def list_catalog(self) -> list[ScopeObject]:
        return [ScopeObject("g1", "Team", "project")]

    async def check_connection(self) -> Diagnosis:
        return Diagnosis(steps=())


def _fake_connector_type(_name: str) -> type[FakeConnector]:
    return FakeConnector


@pytest.fixture(autouse=True)
def fresh_dataset(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    DATASET.__init__()  # reset in place — module-level references stay valid
    monkeypatch.setattr(jobs, "app_settings", test_settings)
    monkeypatch.setattr(jobs, "get_connector_type", _fake_connector_type)


async def _run(
    session: AsyncSession,
    source_id: int,
    mode: SyncMode,
    *,
    trigger: SyncTrigger = SyncTrigger.MANUAL,
) -> int:
    run_id = await sync_runs.start_run(
        session, source_id=source_id, mode=str(mode), trigger=str(trigger)
    )
    await session.commit()
    await jobs.run_sync(CTX, run_id=run_id)
    session.expire_all()
    return run_id


async def _count(session: AsyncSession, model: type) -> int:
    return (await session.scalar(sa.select(sa.func.count()).select_from(model))) or 0


async def test_full_sync_creates_all_projections(db_session: AsyncSession) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    run_id = await _run(db_session, source_id, SyncMode.FULL)

    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.SUCCEEDED)
    assert run.entities_done == 2
    assert run.error_count == 0

    assert await _count(db_session, Entity) == 2
    assert await _count(db_session, Chunk) >= 2
    assert await _count(db_session, SourcePrincipal) == 2
    assert await _count(db_session, Identity) == 2  # bridged by email
    assert await _count(db_session, SourceGroup) == 1
    assert await _count(db_session, GroupMembership) == 2
    assert await _count(db_session, EntityAcl) == 2  # one group grant per entity

    # DOC-1 → DOC-2 was claimed before DOC-2 existed → ref; DOC-2 → DOC-1 → edge.
    assert await _count(db_session, EntityRef) == 1
    assert await _count(db_session, EntityEdge) == 1

    fresh = await db_session.get(Source, source_id)
    assert fresh is not None
    assert fresh.incremental_cursor is not None
    assert fresh.incremental_cursor["since"] == (NOW + timedelta(minutes=5)).isoformat()


async def test_page_boundary_embeds_the_whole_page_in_one_call(
    db_session: AsyncSession, hibp_clean: respx.MockRouter
) -> None:
    """Deferred embed-on-write: the page's chunks go to the encoder as one batch."""
    source = await create_source(db_session, connector_type="fake")
    await assign_embedding(db_session)
    # The assigned model's tokenizer degrades to chars/4 — no HF egress in tests.
    hibp_clean.get(url__startswith="https://huggingface.co/").mock(return_value=httpx.Response(404))
    batch_sizes: list[int] = []

    def responder(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.read())["input"]
        batch_sizes.append(len(texts))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * (EMBEDDING_DIM - 1)}
                    for i in range(len(texts))
                ],
                "usage": {"prompt_tokens": len(texts)},
            },
        )

    hibp_clean.post(EMBEDDINGS_URL).mock(side_effect=responder)

    await _run(db_session, source.id, SyncMode.FULL)

    assert batch_sizes == [2]  # both items' chunks in a single page batch
    unembedded = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Chunk).where(Chunk.embedding.is_(None))
    )
    assert unembedded == 0


async def test_incremental_uses_cursor_and_keeps_untouched_rows(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    await _run(db_session, source_id, SyncMode.FULL)

    doc1_chunk_created = await db_session.scalar(
        sa.select(Chunk.created_at).join(Entity).where(Entity.source_entity_id == "DOC-1")
    )

    DATASET.items[1]["body"] = "Second document, edited."
    DATASET.items[1]["updated"] = NOW + timedelta(hours=1)

    await _run(db_session, source_id, SyncMode.INCREMENTAL)

    assert DATASET.last_since == NOW + timedelta(minutes=5)  # the stored cursor
    edited = await db_session.scalar(
        sa.select(Entity.body).where(Entity.source_entity_id == "DOC-2")
    )
    assert edited == "Second document, edited."
    untouched = await db_session.scalar(
        sa.select(Chunk.created_at).join(Entity).where(Entity.source_entity_id == "DOC-1")
    )
    assert untouched == doc1_chunk_created  # not rewritten


async def test_reconciliation_soft_deletes_vanished_and_reapplies_acl(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    await _run(db_session, source_id, SyncMode.FULL)

    del DATASET.items[1]  # DOC-2 vanished from the source
    DATASET.groups = [GroupDraft("g1", "Team", "project", ("u1",))]  # bob revoked

    await _run(db_session, source_id, SyncMode.RECONCILIATION)

    doc2 = await db_session.scalar(sa.select(Entity).where(Entity.source_entity_id == "DOC-2"))
    assert doc2 is not None
    assert doc2.is_deleted is True
    assert doc2.deleted_at is not None
    doc1 = await db_session.scalar(sa.select(Entity).where(Entity.source_entity_id == "DOC-1"))
    assert doc1 is not None
    assert doc1.is_deleted is False

    assert await _count(db_session, GroupMembership) == 1  # revocation landed


async def test_item_failure_lands_in_dlq_run_stays_partial_success(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    DATASET.poison.add("DOC-2")

    run_id = await _run(db_session, source_id, SyncMode.FULL)

    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.SUCCEEDED)  # partial success, not failure
    assert run.error_count == 1
    assert run.entities_done == 1

    letter = await db_session.scalar(sa.select(DeadLetter))
    assert letter is not None
    assert letter.source_entity_id == "DOC-2"
    assert letter.reason == str(DlqReason.MALFORMED)
    assert letter.attempts == 1

    # A repeat failure bumps attempts on the same row.
    await _run(db_session, source_id, SyncMode.FULL)
    db_session.expire_all()
    letter = await db_session.scalar(sa.select(DeadLetter))
    assert letter is not None
    assert letter.attempts == 2
    assert await _count(db_session, DeadLetter) == 1

    # A successful pass clears the row (resolution is deletion).
    DATASET.poison.clear()
    await _run(db_session, source_id, SyncMode.FULL)
    assert await _count(db_session, DeadLetter) == 0


async def test_dlq_retry_scope_fetches_targeted_items(db_session: AsyncSession) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    DATASET.poison.add("DOC-2")
    await _run(db_session, source_id, SyncMode.FULL)
    DATASET.poison.clear()

    fetched: list[str] = []

    async def fetch_item(self: FakeConnector, source_type: str, source_entity_id: str) -> RawItem:
        del self, source_type
        fetched.append(source_entity_id)
        item = next(i for i in DATASET.items if i["id"] == source_entity_id)
        return RawItem(source_type="doc", source_entity_id=source_entity_id, payload=item)

    FakeConnector.fetch_item = fetch_item  # type: ignore[method-assign]
    try:
        run_id = await sync_runs.start_run(
            db_session,
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.MANUAL),
            scope={"items": [{"source_type": "doc", "source_entity_id": "DOC-2"}]},
        )
        await db_session.commit()
        await jobs.run_sync(CTX, run_id=run_id)
    finally:
        del FakeConnector.fetch_item

    assert fetched == ["DOC-2"]
    db_session.expire_all()
    assert await _count(db_session, DeadLetter) == 0  # resolved by the successful pass


async def test_resume_uses_fresh_checkpoint_of_failed_run(db_session: AsyncSession) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    watermark = (NOW + timedelta(minutes=3)).isoformat()
    db_session.add(
        SyncRun(
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.SCHEDULE),
            state=str(SyncState.FAILED),
            checkpoint={
                "watermark": watermark,
                "done": 7,
                "saved_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    await db_session.commit()

    await _run(db_session, source_id, SyncMode.INCREMENTAL)

    assert DATASET.last_since == NOW + timedelta(minutes=3)  # resumed from the checkpoint


def _failed_run_with_checkpoint(
    source_id: int,
    *,
    mode: SyncMode,
    scope: dict[str, object] | None = None,
) -> SyncRun:
    return SyncRun(
        source_id=source_id,
        mode=str(mode),
        trigger=str(SyncTrigger.SCHEDULE),
        state=str(SyncState.FAILED),
        scope=scope,
        checkpoint={
            "watermark": (NOW + timedelta(minutes=3)).isoformat(),
            "done": 7,
            "saved_at": datetime.now(UTC).isoformat(),
        },
    )


async def test_reconciliation_never_resumes_from_checkpoint(db_session: AsyncSession) -> None:
    """A resumed reconciliation would soft-delete everything before the watermark."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    db_session.add(_failed_run_with_checkpoint(source_id, mode=SyncMode.RECONCILIATION))
    await db_session.commit()

    await _run(db_session, source_id, SyncMode.RECONCILIATION)

    assert DATASET.last_since is None  # the whole source, checkpoint ignored


async def test_targeted_run_checkpoint_is_not_a_resume_point(db_session: AsyncSession) -> None:
    """A DLQ-retry watermark covers only the retried items — never resume from it."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    db_session.add(
        _failed_run_with_checkpoint(
            source_id,
            mode=SyncMode.INCREMENTAL,
            scope={"items": [{"source_type": "doc", "source_entity_id": "DOC-2"}]},
        )
    )
    await db_session.commit()

    await _run(db_session, source_id, SyncMode.INCREMENTAL)

    assert DATASET.last_since is None  # no cursor yet, targeted checkpoint ignored


async def test_unordered_stream_never_resumes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a globally ordered stream the watermark skips items in later containers."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    db_session.add(_failed_run_with_checkpoint(source_id, mode=SyncMode.INCREMENTAL))
    await db_session.commit()
    monkeypatch.setattr(
        FakeConnector, "manifest", replace(FakeConnector.manifest, ordered_stream=False)
    )

    await _run(db_session, source_id, SyncMode.INCREMENTAL)

    assert DATASET.last_since is None  # fell back to the (empty) cursor


async def test_orderly_failure_preserves_checkpoint(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SourceUnavailableError closes the run as failed but keeps its resume point."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    monkeypatch.setattr(runner, "CHECKPOINT_EVERY", 1)

    async def failing_fetch(self: FakeConnector, since: datetime | None) -> AsyncIterator[RawItem]:
        del self, since
        first = DATASET.items[0]
        yield RawItem(source_type="doc", source_entity_id=str(first["id"]), payload=first)
        raise SourceUnavailableError("rate budget exhausted")

    monkeypatch.setattr(FakeConnector, "fetch", failing_fetch)
    run_id = await _run(db_session, source_id, SyncMode.INCREMENTAL)

    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.FAILED)
    assert run.error_detail == "rate budget exhausted"
    assert run.checkpoint is not None  # the next run resumes from here
    assert run.checkpoint["watermark"] == NOW.isoformat()  # DOC-1 landed before the crash


async def test_targeted_retry_success_keeps_cursor(db_session: AsyncSession) -> None:
    """A DLQ-retry watermark must not advance the whole-source cursor (skip-gap)."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    await _run(db_session, source_id, SyncMode.FULL)  # cursor = NOW+5m
    DATASET.items[1]["updated"] = NOW + timedelta(hours=1)

    async def fetch_item(self: FakeConnector, source_type: str, source_entity_id: str) -> RawItem:
        del self, source_type
        item = next(i for i in DATASET.items if i["id"] == source_entity_id)
        return RawItem(source_type="doc", source_entity_id=source_entity_id, payload=item)

    FakeConnector.fetch_item = fetch_item  # type: ignore[method-assign]
    try:
        run_id = await sync_runs.start_run(
            db_session,
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.MANUAL),
            scope={"items": [{"source_type": "doc", "source_entity_id": "DOC-2"}]},
        )
        await db_session.commit()
        await jobs.run_sync(CTX, run_id=run_id)
    finally:
        del FakeConnector.fetch_item

    db_session.expire_all()
    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.SUCCEEDED)
    fresh = await db_session.get(Source, source_id)
    assert fresh is not None
    assert fresh.incremental_cursor is not None
    assert fresh.incremental_cursor["since"] == (NOW + timedelta(minutes=5)).isoformat()


async def test_cursor_never_moves_backwards(db_session: AsyncSession) -> None:
    """An overlap re-fetch with an older watermark must not pull the cursor back."""
    future = (NOW + timedelta(hours=2)).isoformat()
    source = await create_source(
        db_session, connector_type="fake", incremental_cursor={"since": future}
    )
    source_id = source.id

    await _run(db_session, source_id, SyncMode.FULL)  # watermark NOW+5m < cursor

    fresh = await db_session.get(Source, source_id)
    assert fresh is not None
    assert fresh.incremental_cursor is not None
    assert fresh.incremental_cursor["since"] == future


async def test_dlq_item_survives_reconciliation(db_session: AsyncSession) -> None:
    """An item that failed this sweep did not vanish — soft delete would be data loss."""
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    await _run(db_session, source_id, SyncMode.FULL)
    DATASET.poison.add("DOC-2")  # fails the sweep, but still exists on the source

    await _run(db_session, source_id, SyncMode.RECONCILIATION)

    doc2 = await db_session.scalar(sa.select(Entity).where(Entity.source_entity_id == "DOC-2"))
    assert doc2 is not None
    assert doc2.is_deleted is False
    assert await _count(db_session, DeadLetter) == 1


async def test_stale_checkpoint_falls_back_to_cursor(db_session: AsyncSession) -> None:
    source = await create_source(
        db_session,
        connector_type="fake",
        incremental_cursor={"since": NOW.isoformat()},
    )
    source_id = source.id
    db_session.add(
        SyncRun(
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.SCHEDULE),
            state=str(SyncState.FAILED),
            checkpoint={
                "watermark": (NOW + timedelta(minutes=3)).isoformat(),
                "done": 7,
                "saved_at": (datetime.now(UTC) - timedelta(hours=7)).isoformat(),
            },
        )
    )
    await db_session.commit()

    await _run(db_session, source_id, SyncMode.INCREMENTAL)

    assert DATASET.last_since == NOW  # stale checkpoint ignored, cursor used


async def test_cancelled_run_stops_at_page_boundary(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await create_source(db_session, connector_type="fake")
    source_id = source.id
    monkeypatch.setattr(runner, "CHECKPOINT_EVERY", 1)

    run_id = await sync_runs.start_run(
        db_session, source_id=source_id, mode=str(SyncMode.FULL), trigger=str(SyncTrigger.MANUAL)
    )
    await db_session.commit()

    async def cancelling_fetch(
        self: FakeConnector, since: datetime | None
    ) -> AsyncIterator[RawItem]:
        del self, since
        first = DATASET.items[0]
        yield RawItem(source_type="doc", source_entity_id=str(first["id"]), payload=first)
        # The admin presses cancel mid-run; the page boundary must notice it.
        db = create_connections(jobs.app_settings)
        try:
            async with db.pg_session_factory() as s, s.begin():
                await sync_runs.cancel(s, run_id)
        finally:
            await close_connections(db)
        second = DATASET.items[1]
        yield RawItem(source_type="doc", source_entity_id=str(second["id"]), payload=second)

    monkeypatch.setattr(FakeConnector, "fetch", cancelling_fetch)
    await jobs.run_sync(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.state == str(SyncState.CANCELLED)
    # The second item never landed: the boundary after item 1 noticed the cancel.
    assert (
        await db_session.scalar(sa.select(Entity).where(Entity.source_entity_id == "DOC-2"))
    ) is None
