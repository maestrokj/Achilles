"""run_curation chain, lane coordination gates, run_reembed (P0)."""

import json
from typing import cast

import httpx
import pytest
import respx
import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.ai import (
    EMBEDDINGS_LOAD_URL,
    EMBEDDINGS_STATUS_URL,
    EMBEDDINGS_URL,
    assign_embedding,
)
from tests.factories.knowledge import create_chunk, create_entity, create_source

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.config import Settings
from achilles.db.connections import close_connections, create_connections
from achilles.harvester.constants import SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import SyncRun
from achilles.knowledge_store import jobs
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import Chunk, CurationRun, EntityRef
from achilles.knowledge_store.services import curation, curation_steps
from achilles.knowledge_store.services.curation_steps import EmbeddingRuntimeUnavailableError

pytestmark = [pytest.mark.integration, pytest.mark.p0]

CTX = cast("Context", {})


@pytest.fixture(autouse=True)
def jobs_use_test_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


async def _running_sync(session: AsyncSession, source_id: int) -> int:
    run = SyncRun(
        source_id=source_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.SCHEDULE),
        state=str(SyncState.RUNNING),
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    await session.commit()
    return run_id


async def test_run_curation_executes_chain_and_journals_steps(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session)
    src = await create_entity(db_session, source_id=source.id, source_entity_id="R-1")
    await create_entity(db_session, source_id=source.id, source_type="page", source_entity_id="R-2")
    db_session.add(
        EntityRef(src_entity_id=src.id, relation="mentions", target_kind="page", target_ref="R-2")
    )
    await db_session.commit()
    run_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MANUAL))
    await db_session.commit()

    await jobs.run_curation(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == str(CurationState.SUCCEEDED)
    assert run.steps == {
        "refs_materialized": 1,
        "duplicates_merged": 0,
        "entities_rescored": 2,
    }
    assert run.destructive_since is None  # the window never leaks past the run


async def test_open_destructive_window_yields_to_running_sync(
    db_session: AsyncSession,
) -> None:
    source = await create_source(db_session)
    run_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MANUAL))
    assert await curation.mark_running(db_session, run_id)
    await db_session.commit()

    sync_id = await _running_sync(db_session, source.id)
    assert await curation.open_destructive_window(db_session, run_id) is False
    await db_session.rollback()

    # The sync finishes — the window opens.
    await db_session.execute(
        sa.update(SyncRun).where(SyncRun.id == sync_id).values(state=str(SyncState.SUCCEEDED))
    )
    await db_session.commit()
    assert await curation.open_destructive_window(db_session, run_id) is True
    await db_session.commit()

    await curation.close_destructive_window(db_session, run_id)
    await db_session.commit()
    db_session.expire_all()
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.destructive_since is None


async def test_merge_step_skips_when_sync_keeps_the_lane_busy(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    source = await create_source(db_session)
    await _running_sync(db_session, source.id)
    run_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MANUAL))
    assert await curation.mark_running(db_session, run_id)
    await db_session.commit()

    db = create_connections(test_settings)
    steps: dict[str, object] = {}
    errors: list[str] = []
    try:
        await jobs._merge_step(db, run_id, steps, errors, wait_retry=0.05, wait_cap=0.2)
    finally:
        await close_connections(db)

    assert steps == {"duplicates_merged": "skipped"}
    assert errors == []


async def test_run_reembed_updates_stale_chunks(
    db_session: AsyncSession, hibp_clean: respx.MockRouter
) -> None:
    await assign_embedding(db_session)
    source = await create_source(db_session)
    entity_id = (await create_entity(db_session, source_id=source.id)).id
    stale_id = (
        await create_chunk(
            db_session,
            entity_id=entity_id,
            ordinal=0,
            text="stale vector",
            embedding=[0.5] * EMBEDDING_DIM,
            embedding_model="old-model",
        )
    ).id
    missing_id = (
        await create_chunk(db_session, entity_id=entity_id, ordinal=1, text="no vector")
    ).id

    def responder(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.read())["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * (EMBEDDING_DIM - 1)}
                    for i in range(len(texts))
                ],
                "usage": {"prompt_tokens": 5 * len(texts)},
            },
        )

    hibp_clean.post(EMBEDDINGS_URL).mock(side_effect=responder)

    run_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MODEL_CHANGE))
    await db_session.commit()
    await jobs.run_reembed(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == str(CurationState.SUCCEEDED)
    assert run.steps == {"reembedded": 2}

    models = (
        await db_session.execute(
            sa.select(Chunk.id, Chunk.embedding_model).where(Chunk.id.in_([stale_id, missing_id]))
        )
    ).all()
    assert all(model == "BAAI/bge-m3" for _, model in models)

    # Idempotent: a second run finds nothing stale.
    run2 = await curation.start_run(db_session, trigger=str(CurationTrigger.MANUAL))
    await db_session.commit()
    await jobs.run_reembed(CTX, run_id=run2)
    db_session.expire_all()
    second = await db_session.get(CurationRun, run2)
    assert second is not None
    assert second.steps == {"reembedded": 0}


def _status_response(state: str, error: str | None = None) -> httpx.Response:
    """GET /admin/status with the builtin model in the given state."""
    return httpx.Response(
        200,
        json={
            "budget_bytes": 1,
            "desired": "BAAI/bge-m3",
            "models": {"BAAI/bge-m3": {"state": state, "error": error}},
        },
    )


async def test_reembed_waits_out_a_loading_runtime(
    db_session: AsyncSession, hibp_clean: respx.MockRouter, test_settings: Settings
) -> None:
    """A runtime reporting `loading` is waited out on its own budget: the two
    503s here exceed max_retries=1, yet the batch survives — loading polls
    must not burn the stall budget."""
    await assign_embedding(db_session)
    source = await create_source(db_session)
    entity_id = (await create_entity(db_session, source_id=source.id)).id
    chunk_id = (
        await create_chunk(
            db_session, entity_id=entity_id, ordinal=0, text="stale", embedding_model="old-model"
        )
    ).id
    await db_session.commit()

    hibp_clean.get(EMBEDDINGS_STATUS_URL).mock(return_value=_status_response("loading"))
    calls = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:  # runtime still warming the new weights
            return httpx.Response(503, json={"detail": "model is loading"})
        texts = json.loads(request.read())["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * (EMBEDDING_DIM - 1)}
                    for i in range(len(texts))
                ],
                "usage": {"prompt_tokens": 5 * len(texts)},
            },
        )

    hibp_clean.post(EMBEDDINGS_URL).mock(side_effect=responder)

    db = create_connections(test_settings)
    try:
        embedded = await curation_steps.reembed_batches(
            db.pg_session_factory, retry_wait=0.01, max_retries=1, loading_poll=0.01
        )
    finally:
        await close_connections(db)

    assert embedded == 1
    assert calls["n"] == 3  # two 503s waited out, third succeeded
    db_session.expire_all()
    chunk = await db_session.get(Chunk, chunk_id)
    assert chunk is not None
    assert chunk.embedding_model == "BAAI/bge-m3"


async def test_reembed_fails_fast_when_runtime_reports_error(
    db_session: AsyncSession, hibp_clean: respx.MockRouter, test_settings: Settings
) -> None:
    """A load the runtime itself gave up on cannot be waited out — fail at once."""
    await assign_embedding(db_session)
    source = await create_source(db_session)
    entity_id = (await create_entity(db_session, source_id=source.id)).id
    await create_chunk(
        db_session, entity_id=entity_id, ordinal=0, text="stale", embedding_model="old-model"
    )
    await db_session.commit()
    hibp_clean.post(EMBEDDINGS_URL).mock(return_value=httpx.Response(503))
    hibp_clean.get(EMBEDDINGS_STATUS_URL).mock(
        return_value=_status_response("error", "weights corrupted")
    )

    db = create_connections(test_settings)
    try:
        with pytest.raises(EmbeddingRuntimeUnavailableError, match="weights corrupted"):
            await curation_steps.reembed_batches(
                db.pg_session_factory, retry_wait=0.01, max_retries=5
            )
    finally:
        await close_connections(db)


async def test_reembed_gives_up_when_loading_exceeds_its_budget(
    db_session: AsyncSession, hibp_clean: respx.MockRouter, test_settings: Settings
) -> None:
    """`loading` forever (a wedged download) still ends: the loading budget caps it."""
    await assign_embedding(db_session)
    source = await create_source(db_session)
    entity_id = (await create_entity(db_session, source_id=source.id)).id
    await create_chunk(
        db_session, entity_id=entity_id, ordinal=0, text="stale", embedding_model="old-model"
    )
    await db_session.commit()
    hibp_clean.post(EMBEDDINGS_URL).mock(return_value=httpx.Response(503))
    hibp_clean.get(EMBEDDINGS_STATUS_URL).mock(return_value=_status_response("loading"))

    db = create_connections(test_settings)
    try:
        with pytest.raises(EmbeddingRuntimeUnavailableError, match="still loading"):
            await curation_steps.reembed_batches(
                db.pg_session_factory,
                retry_wait=0.01,
                max_retries=5,
                loading_poll=0.01,
                loading_max=0.03,
            )
    finally:
        await close_connections(db)


async def test_reembed_gives_up_when_runtime_stays_down(
    db_session: AsyncSession, hibp_clean: respx.MockRouter, test_settings: Settings
) -> None:
    """Runtime silent past the wait budget → raise, so the run fails honestly."""
    await assign_embedding(db_session)
    source = await create_source(db_session)
    entity_id = (await create_entity(db_session, source_id=source.id)).id
    await create_chunk(
        db_session, entity_id=entity_id, ordinal=0, text="stale", embedding_model="old-model"
    )
    await db_session.commit()
    hibp_clean.post(EMBEDDINGS_URL).mock(return_value=httpx.Response(503))
    hibp_clean.post(EMBEDDINGS_LOAD_URL).mock(
        return_value=httpx.Response(200, json={"model_id": "BAAI/bge-m3", "status": "loading"})
    )
    # The status probe finds nobody home either — the stall budget owns this.
    hibp_clean.get(EMBEDDINGS_STATUS_URL).mock(side_effect=httpx.ConnectError("down"))

    db = create_connections(test_settings)
    try:
        with pytest.raises(EmbeddingRuntimeUnavailableError, match="unanswered"):
            await curation_steps.reembed_batches(
                db.pg_session_factory, retry_wait=0.01, max_retries=2
            )
    finally:
        await close_connections(db)


async def test_run_reembed_fails_when_runtime_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_reembed terminates as failed (not a 0-chunk success) on an unavailable runtime."""

    async def _raise(*_args: object, **_kwargs: object) -> int:
        raise EmbeddingRuntimeUnavailableError("runtime down")

    monkeypatch.setattr(curation_steps, "reembed_batches", _raise)
    run_id = await curation.start_run(db_session, trigger=str(CurationTrigger.MODEL_CHANGE))
    await db_session.commit()

    await jobs.run_reembed(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get(CurationRun, run_id)
    assert run is not None
    assert run.state == str(CurationState.FAILED)
    assert run.error == "runtime down"  # the loop's own diagnosis, verbatim
