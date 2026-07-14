"""GET /admin/dashboard: tile composition, attention signals, RBAC (dashboard.html)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.harvester.constants import DlqReason, SyncMode, SyncState, SyncTrigger
from achilles.harvester.models import DeadLetter, SyncRun
from achilles.knowledge_store.constants import BackupState
from achilles.knowledge_store.services import backups
from tests.auth.integration.conftest import AuthorizeFn, set_smtp
from tests.conftest import FlushRedis
from tests.factories.admin import set_platform_settings
from tests.factories.agents import allow_agent_model, create_agent
from tests.factories.ai import (
    allow_chat,
    assign_embedding,
    create_model,
    create_usage,
    reset_ai_catalog,
)
from tests.factories.knowledge import (
    create_chunk,
    create_entity,
    create_identity,
    create_principal,
    create_source,
)
from tests.factories.users import create_user
from tests.knowledge_store.conftest import KS_TABLES, RESET_PLATFORM_SETTINGS

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/dashboard"

_TABLES = ("agent_runs", "agent_tools", "agents", "chat_models", "agent_models", *KS_TABLES)

# The setup tile reads the seeded settings singletons and the assignment table —
# pin them all to the untouched state (reset by UPDATE/DELETE, never TRUNCATE).
_RESET_SETUP_FACTS = (
    "UPDATE smtp_settings SET is_enabled = false, host = NULL, port = NULL,"
    " from_address = NULL WHERE id = 1",
    "UPDATE slack_settings SET enabled = false WHERE id = 1",
    "UPDATE telegram_settings SET enabled = false WHERE id = 1",
    "UPDATE mattermost_settings SET enabled = false WHERE id = 1",
    "DELETE FROM model_assignments",
)


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    """Overrides the package conftest: the dashboard reads across many domains."""
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
        await conn.execute(sa.text(RESET_PLATFORM_SETTINGS))
        for statement in _RESET_SETUP_FACTS:
            await conn.execute(sa.text(statement))
    await flush_redis()


async def _finished_sync(session: AsyncSession, *, source_id: int, state: SyncState) -> None:
    now = datetime.now(UTC)
    session.add(
        SyncRun(
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.MANUAL),
            state=str(state),
            started_at=now - timedelta(minutes=5),
            finished_at=now,
        )
    )
    await session.commit()


async def test_owner_sees_the_full_composition(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    source = await create_source(db_session)
    entity = await create_entity(db_session, source_id=source.id)
    await create_chunk(db_session, entity_id=entity.id)
    allowed = await allow_agent_model(db_session)
    await create_agent(db_session, user_id=owner.id, model_id=allowed.id)
    snapshot_id = await backups.start_snapshot(db_session)
    await backups.finish_snapshot(
        db_session, snapshot_id, state=str(BackupState.SUCCEEDED), size_bytes=1024
    )
    await db_session.commit()
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    assert body["org_name"] == "Achilles"
    assert body["is_empty"] is False
    assert body["users"]["total"] == 1
    assert body["sources"] == {
        "total": 1,
        "active": 1,
        "paused": 0,
        "disconnected": 0,
        "failing": 0,
    }
    assert body["knowledge"]["entities"] == 1
    assert body["agents"]["total"] == 1
    assert body["last_backup"]["state"] == "succeeded"
    assert body["last_sync"] is None  # no run yet for this source
    assert body["curation"] is None
    assert isinstance(body["audit"], list)  # Owner carries AUDIT_READ
    assert body["attention"] == []


async def test_last_sync_reflects_the_freshest_run(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    done = await create_source(db_session, name="Wiki")
    await _finished_sync(db_session, source_id=done.id, state=SyncState.SUCCEEDED)
    live = await create_source(db_session, name="Jira")
    db_session.add(
        SyncRun(
            source_id=live.id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.SCHEDULE),
            state=str(SyncState.RUNNING),
            entities_done=300,
            started_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    # The running run has the greater id — it wins, and one source is in flight.
    assert body["last_sync"]["state"] == "running"
    assert body["last_sync"]["entities"] == 300
    assert body["last_sync"]["running"] == 1


async def test_attention_signals_sort_critical_first(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    failing = await create_source(db_session, name="Broken Jira")
    await _finished_sync(db_session, source_id=failing.id, state=SyncState.FAILED)
    dlq_source = await create_source(db_session, name="Wiki")
    db_session.add(
        DeadLetter(
            source_id=dlq_source.id,
            source_type="page",
            source_entity_id="p-1",
            reason=str(DlqReason.PERMISSION),
        )
    )
    snapshot_id = await backups.start_snapshot(db_session)
    await backups.finish_snapshot(
        db_session, snapshot_id, state=str(BackupState.FAILED), error="boom"
    )
    await db_session.commit()
    # 80% of the monthly budget is reached — the advisory warning fires.
    await set_platform_settings(db_session, ai_monthly_budget=100)
    model_id = await db_session.scalar(sa.text("SELECT min(id) FROM ai_models"))
    assert model_id is not None
    await create_usage(
        db_session,
        model_id=int(model_id),
        bucket_date=datetime.now(UTC).date(),
        input_tokens=1000,
        request_count=1,
        cost=80,
    )
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    kinds = [(item["severity"], item["kind"]) for item in body["attention"]]
    assert ("critical", "source_failing") in kinds
    assert ("critical", "backup_failed") in kinds
    assert ("warning", "dlq") in kinds
    assert ("warning", "budget") in kinds
    severities = [item["severity"] for item in body["attention"]]
    assert severities == sorted(severities, key=lambda s: s != "critical")
    failing_item = next(i for i in body["attention"] if i["kind"] == "source_failing")
    assert failing_item["subject"] == "Broken Jira"
    assert failing_item["source_id"] == failing.id
    dlq_item = next(i for i in body["attention"] if i["kind"] == "dlq")
    assert dlq_item["source_id"] == dlq_source.id
    budget_item = next(i for i in body["attention"] if i["kind"] == "budget")
    assert budget_item["source_id"] is None


async def test_admin_gets_no_audit_and_member_is_403(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)

    await authorize(admin.email)
    body = (await client.get(URL)).json()
    assert body["audit"] is None

    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403


async def test_empty_platform_reads_as_valid_state(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    assert body["is_empty"] is True
    assert body["sources"]["total"] == 0
    assert body["last_backup"] is None
    assert body["last_sync"] is None
    assert body["tasks"] == {"pending_invites": 0, "unmatched_identities": 1}  # the owner himself


async def test_setup_tile_tracks_first_run_configuration(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    assert body["setup"] == {
        "email": False,
        "surfaces": False,
        "embedding": False,
        "chat_models": False,
        "agent_models": False,
    }

    await set_smtp(db_session, enabled=True)
    # One available surface is enough — enabled + the secrets a send needs.
    await db_session.execute(
        sa.text(
            "UPDATE telegram_settings SET enabled = true, bot_token_enc = 'tok',"
            " webhook_secret_enc = 'sec' WHERE id = 1"
        )
    )
    await db_session.commit()
    await assign_embedding(db_session)
    chat = await create_model(db_session)
    await allow_chat(db_session, chat.id)
    await allow_agent_model(db_session, chat.id)

    body = (await client.get(URL)).json()
    assert body["setup"] == {
        "email": True,
        "surfaces": True,
        "embedding": True,
        "chat_models": True,
        "agent_models": True,
    }


async def test_disabled_surface_does_not_count_as_configured(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    # Secrets are stored but the master switch is off — not available, not configured.
    await db_session.execute(
        sa.text(
            "UPDATE slack_settings SET enabled = false, team = 'T1', bot_token_enc = 'tok',"
            " signing_secret_enc = 'sec' WHERE id = 1"
        )
    )
    await db_session.commit()
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    assert body["setup"]["surfaces"] is False


async def test_unmatched_identities_counts_users_without_a_linked_account(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    await create_user(db_session)
    matched = await create_user(db_session)
    source = await create_source(db_session)
    identity = await create_identity(db_session, user_id=matched.id)
    await create_principal(db_session, source_id=source.id, identity_id=identity.id)
    await authorize(owner.email)

    body = (await client.get(URL)).json()
    assert body["tasks"]["unmatched_identities"] == 2, "the owner and the plain member"
