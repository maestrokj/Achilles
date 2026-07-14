"""Source management API: access control, CRUD, sync lifecycle, DLQ (tests.html)."""

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import UserRole
from achilles.harvester.constants import DlqReason, SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import DeadLetter, SyncRun
from achilles.harvester.services import sync_runs
from achilles.knowledge_store.models import Entity, Source
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import assign_embedding
from tests.factories.knowledge import create_source
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/sources"

CREATE_BODY = {
    "name": "Team Jira",
    "connector_type": "jira",
    "base_url": "https://jira.example.test",
    "credential": "bot@example.com:secret-token",
}


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


@pytest.fixture
async def as_member(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)


@pytest.fixture
async def embedder_assigned(db_session: AsyncSession) -> None:
    await assign_embedding(db_session)


async def test_anonymous_is_401(client: AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 401


@pytest.mark.usefixtures("as_admin")
async def test_connector_registry_lists_manifests(client: AsyncClient) -> None:
    """Wizard step 1: the built-in four with their manifest facts, not `{source_id}`-shadowed."""
    body = (await client.get(f"{BASE}/connectors")).json()
    by_type = {item["type"]: item for item in body}
    assert {"jira", "confluence", "slack", "gitlab"} <= set(by_type)
    jira = by_type["jira"]
    assert jira["needs_base_url"] is True
    assert jira["credential_label"]
    assert isinstance(jira["scope_kinds"], list)
    assert isinstance(jira["collection_toggles"], list)


@pytest.mark.usefixtures("as_member")
async def test_connector_registry_is_admin_only(client: AsyncClient) -> None:
    assert (await client.get(f"{BASE}/connectors")).status_code == 403


@pytest.mark.usefixtures("as_admin")
@respx.mock
async def test_probe_draft_returns_diagnosis_and_catalog(client: AsyncClient) -> None:
    """Wizard step 3: a green probe on a draft (no source row) carries the catalog along."""
    jira_base = "https://acme.atlassian.test"
    respx.get(f"{jira_base}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "acc-1"})
    )
    respx.get(f"{jira_base}/rest/api/2/project").mock(
        return_value=httpx.Response(200, json=[{"key": "ENG", "name": "Engineering"}])
    )

    body = (
        await client.post(
            f"{BASE}/probe",
            json={"connector_type": "jira", "base_url": jira_base, "credential": "u:t"},
        )
    ).json()
    assert body["ok"] is True
    assert body["catalog"] == [{"native_id": "ENG", "name": "Engineering", "kind": "project"}]

    sources = (await client.get(BASE)).json()
    assert sources == []  # the draft never touched the table


@pytest.mark.usefixtures("as_admin")
async def test_probe_requires_base_url_when_manifest_says_so(client: AsyncClient) -> None:
    response = await client.post(f"{BASE}/probe", json={"connector_type": "jira"})
    assert response.status_code == 422


async def test_member_is_403(client: AsyncClient, as_member: None) -> None:
    assert (await client.get(BASE)).status_code == 403
    assert (await client.post(BASE, json=CREATE_BODY)).status_code == 403


async def test_create_requires_embedding_assignment(client: AsyncClient, as_admin: None) -> None:
    resp = await client.post(BASE, json=CREATE_BODY)
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMBEDDINGS_UNAVAILABLE"


async def test_create_starts_auto_full_sync(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_durable: Redis,
    as_admin: None,
    embedder_assigned: None,
) -> None:
    resp = await client.post(BASE, json=CREATE_BODY)
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["credential_is_set"] is True
    assert "credential" not in payload  # write-only secret
    assert payload["authority_tier"] == "normal"  # manifest default
    assert payload["health"] == "queued"  # the auto full sync waits for the worker
    assert payload["last_run"]["state"] == "queued"
    assert payload["last_run"]["mode"] == "full"

    run = await db_session.scalar(sa.select(SyncRun).where(SyncRun.source_id == payload["id"]))
    assert run is not None
    assert run.mode == str(SyncMode.FULL)
    assert run.trigger == str(SyncTrigger.CONNECT)
    assert await redis_durable.exists(f"dedup:job:sync:{run.id}")


async def test_create_unknown_connector_is_422(
    client: AsyncClient, as_admin: None, embedder_assigned: None
) -> None:
    resp = await client.post(BASE, json={**CREATE_BODY, "connector_type": "nope"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "UNKNOWN_CONNECTOR"


async def test_create_without_required_base_url_is_422(
    client: AsyncClient, as_admin: None, embedder_assigned: None
) -> None:
    resp = await client.post(BASE, json={"name": "J", "connector_type": "jira"})
    assert resp.status_code == 422


async def test_entity_count_reports_live_contribution(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session, name="Graph Jira")
    db_session.add_all(
        [
            Entity(source_id=source.id, source_type="issue", source_entity_id="A-1", title="a"),
            Entity(source_id=source.id, source_type="issue", source_entity_id="A-2", title="b"),
            # A reconciliation-deleted entity is no longer in the graph — excluded.
            Entity(
                source_id=source.id,
                source_type="issue",
                source_entity_id="A-3",
                title="c",
                is_deleted=True,
            ),
        ]
    )
    await db_session.commit()

    (row,) = (await client.get(BASE)).json()
    assert row["entity_count"] == 2


async def test_patch_secret_semantics_and_pause(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session, credential_enc="ciphertext")
    source_id = source.id

    resp = await client.patch(f"{BASE}/{source_id}", json={"state": "paused", "name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "paused"
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["credential_is_set"] is True  # None = keep

    resp = await client.patch(f"{BASE}/{source_id}", json={"credential": ""})
    assert resp.json()["credential_is_set"] is False  # "" = clear

    resp = await client.patch(f"{BASE}/{source_id}", json={"state": "disconnected"})
    assert resp.status_code == 422  # derived state, not settable


async def test_manual_sync_202_and_409_under_lock(
    client: AsyncClient, db_session: AsyncSession, redis_durable: Redis, as_admin: None
) -> None:
    source = await create_source(db_session, connector_type="jira")
    source_id = source.id

    resp = await client.post(f"{BASE}/{source_id}/sync", json={"mode": "incremental"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert await redis_durable.exists(f"dedup:job:sync:{run_id}")

    duplicate = await client.post(f"{BASE}/{source_id}/sync", json={"mode": "full"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "RUN_ALREADY_ACTIVE"


async def test_auto_modes_not_exposed(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    resp = await client.post(f"{BASE}/{source.id}/sync", json={"mode": "reconciliation"})
    assert resp.status_code == 422


async def test_cancel_terminalizes_active_run(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    run_id = await sync_runs.start_run(
        db_session,
        source_id=source_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.MANUAL),
    )
    await db_session.commit()

    resp = await client.post(f"{BASE}/{source_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id
    db_session.expire_all()
    assert await sync_runs.get_state(db_session, run_id) == str(SyncState.CANCELLED)

    again = await client.post(f"{BASE}/{source_id}/cancel")
    assert again.status_code == 404  # nothing active anymore


async def test_delete_requires_exact_name_confirmation(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session, name="Prod Jira")
    source_id = source.id
    db_session.add(
        Entity(source_id=source_id, source_type="issue", source_entity_id="X-1", title="t")
    )
    await db_session.commit()

    resp = await client.request("DELETE", f"{BASE}/{source_id}", json={"confirm": "prod jira"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "CONFIRM_MISMATCH"

    resp = await client.request("DELETE", f"{BASE}/{source_id}", json={"confirm": "Prod Jira"})
    assert resp.status_code == 204
    db_session.expire_all()
    assert await db_session.get(Source, source_id) is None
    # FK CASCADE took the content along (config + data removal).
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(Entity))) == 0


async def test_runs_history_and_health(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    run_id = await sync_runs.start_run(
        db_session,
        source_id=source_id,
        mode=str(SyncMode.FULL),
        trigger=str(SyncTrigger.CONNECT),
    )
    await sync_runs.finish(db_session, run_id, state=str(SyncState.FAILED), error_detail="boom")
    await db_session.commit()

    runs = (await client.get(f"{BASE}/{source_id}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["state"] == "failed"
    assert runs[0]["error_detail"] == "boom"

    health = (await client.get(f"{BASE}/{source_id}/health")).json()
    assert health["health"] == "error"  # last run failed
    assert health["active_run_id"] is None


async def test_last_run_summary_of_terminal_run(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    run_id = await sync_runs.start_run(
        db_session,
        source_id=source_id,
        mode=str(SyncMode.FULL),
        trigger=str(SyncTrigger.MANUAL),
    )
    await sync_runs.mark_running(db_session, run_id)
    await sync_runs.update_progress(db_session, run_id, entities_done=5, entities_total=9)
    await sync_runs.finish(db_session, run_id, state=str(SyncState.FAILED), error_detail="boom")
    await db_session.commit()

    body = (await client.get(f"{BASE}/{source_id}")).json()
    assert body["health"] == "error"
    last_run = body["last_run"]
    assert last_run["state"] == "failed"
    assert last_run["mode"] == "full"
    assert last_run["error"] == "boom"
    assert last_run["progress_done"] == 5
    assert last_run["progress_total"] == 9
    assert last_run["duration_seconds"] >= 0


async def test_last_run_is_null_without_history(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    body = (await client.get(f"{BASE}/{source.id}")).json()
    assert body["last_run"] is None
    assert body["health"] == "idle"


async def test_health_reports_queued_run(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    await sync_runs.start_run(
        db_session,
        source_id=source_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.MANUAL),
    )
    await db_session.commit()

    health = (await client.get(f"{BASE}/{source_id}/health")).json()
    assert health["health"] == "queued"


async def test_dead_letters_list_and_retry(
    client: AsyncClient, db_session: AsyncSession, redis_durable: Redis, as_admin: None
) -> None:
    source = await create_source(db_session)
    source_id = source.id
    db_session.add(
        DeadLetter(
            source_id=source_id,
            source_type="issue",
            source_entity_id="X-13",
            reason=str(DlqReason.PERMISSION),
            error_detail="403",
        )
    )
    await db_session.commit()

    listed = (await client.get(f"{BASE}/{source_id}/dead-letters")).json()
    assert len(listed) == 1
    assert listed[0]["source_entity_id"] == "X-13"

    resp = await client.post(f"{BASE}/{source_id}/dead-letters/retry")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    run = await db_session.get(SyncRun, run_id)
    assert run is not None
    assert run.mode == str(SyncMode.INCREMENTAL)
    assert run.trigger == str(SyncTrigger.MANUAL)
    assert run.scope == {"items": [{"source_type": "issue", "source_entity_id": "X-13"}]}
    assert await redis_durable.exists(f"dedup:job:sync:{run_id}")


async def test_retry_with_empty_queue_is_404(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    source = await create_source(db_session)
    resp = await client.post(f"{BASE}/{source.id}/dead-letters/retry")
    assert resp.status_code == 404


async def test_sync_all_fans_out_over_active_sources(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
) -> None:
    active = await create_source(db_session)
    active_id = active.id
    paused = await create_source(db_session, state="paused")
    busy = await create_source(db_session)
    busy_id = busy.id
    await sync_runs.start_run(
        db_session,
        source_id=busy_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.SCHEDULE),
    )
    await db_session.commit()
    del paused

    resp = await client.post(f"{BASE}/sync")
    assert resp.status_code == 202
    run_ids = resp.json()["run_ids"]
    assert len(run_ids) == 1  # only the idle active source
    run = await db_session.get(SyncRun, run_ids[0])
    assert run is not None
    assert run.source_id == active_id
