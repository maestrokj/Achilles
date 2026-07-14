"""The grooming panel API: status, cancel, re-embed progress (knowledge-store.html)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import assign_embedding
from tests.factories.knowledge import create_chunk, create_entity, create_source
from tests.factories.users import create_user

from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.services import curation

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/knowledge/curation"
REINDEX_URL = "/api/v1/admin/knowledge/reindex"


async def test_idle_status_shows_the_next_window(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    assert body["active"] is None
    assert body["last"] is None
    assert body["reembed"] is None
    assert body["next_scheduled"] is not None  # daily 04:00 seed always yields a moment


async def test_manual_start_appears_as_active(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)

    run_id = (await client.post(REINDEX_URL)).json()["run_id"]
    body = (await client.get(URL)).json()
    assert body["active"]["id"] == run_id
    assert body["active"]["trigger"] == str(CurationTrigger.MANUAL)
    assert body["active"]["state"] == str(CurationState.QUEUED)
    assert body["active"]["destructive_open"] is False
    assert body["reembed"] is None  # a grooming run carries no re-embed panel


async def test_cancel_transitions_and_audits(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    await authorize(admin.email)
    run_id = (await client.post(REINDEX_URL)).json()["run_id"]

    assert (await client.post(f"{URL}/{run_id}/cancel")).status_code == 200
    body = (await client.get(URL)).json()
    assert body["active"] is None
    assert body["last"]["id"] == run_id
    assert body["last"]["state"] == str(CurationState.CANCELLED)

    audited = await db_session.scalar(
        sa.text("SELECT count(*) FROM audit_log WHERE action = 'knowledge.curation_cancel'")
    )
    assert audited == 1

    # Cancelling a finished run is a conflict, an unknown one — not found.
    conflict = await client.post(f"{URL}/{run_id}/cancel")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "RUN_ALREADY_FINISHED"
    assert (await client.post(f"{URL}/9999/cancel")).status_code == 404


async def test_reembed_run_reports_progress(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    model = await assign_embedding(db_session)
    source = await create_source(db_session)
    fresh = await create_entity(db_session, source_id=source.id)
    stale = await create_entity(db_session, source_id=source.id)
    await create_chunk(
        db_session, entity_id=fresh.id, embedding=[0.1] * 1024, embedding_model=model.model_id
    )
    await create_chunk(
        db_session, entity_id=stale.id, embedding=[0.1] * 1024, embedding_model="retired-model"
    )
    await curation.start_run(db_session, trigger=str(CurationTrigger.MODEL_CHANGE))
    await db_session.commit()
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    assert body["active"]["trigger"] == str(CurationTrigger.MODEL_CHANGE)
    assert body["reembed"] == {
        "done": 1,
        "total": 2,
        # The catalog row for the retired id is gone — the honest raw id remains.
        "from_model": "retired-model",
        "to_model": model.display_name,
    }


async def test_member_cannot_see_or_cancel(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403
    assert (await client.post(f"{URL}/1/cancel")).status_code == 403
