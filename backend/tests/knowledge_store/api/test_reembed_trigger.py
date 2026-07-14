"""Changing the harvester_embedding assignment kicks an embedding-refresh run (P1).

Lives in the KS scope: curation_runs isolation is here, and the refresh run
is a KS journal row — the AI route only pulls the trigger.
"""

import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import create_model, get_builtin_model
from tests.factories.knowledge import create_chunk, create_entity, create_source
from tests.factories.users import create_user

from achilles.auth.constants import UserRole
from achilles.knowledge_store.constants import CurationState, CurationTrigger
from achilles.knowledge_store.models import CurationRun

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


@pytest.fixture
async def as_admin(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    admin = await create_user(db_session, role=UserRole.ADMIN.value)
    await authorize(admin.email)


@pytest.fixture(autouse=True)
def embeddings_offline(hibp_clean: respx.MockRouter) -> None:
    """The warm-up call is best-effort; an unreachable runtime must not matter."""
    hibp_clean.post(url__startswith="http://embeddings").mock(
        return_value=respx.MockResponse(200, json={})
    )


async def _stale_chunk(session: AsyncSession) -> None:
    """One live chunk embedded by a retired model — the refresh has real work."""
    source = await create_source(session)
    entity = await create_entity(session, source_id=source.id)
    await create_chunk(
        session, entity_id=entity.id, ordinal=0, text="stale", embedding_model="old-model"
    )
    await session.commit()


async def test_assignment_change_starts_model_change_run(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_durable: Redis,
    as_admin: None,
) -> None:
    await _stale_chunk(db_session)
    builtin = await get_builtin_model(db_session)
    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200

    run = await db_session.scalar(sa.select(CurationRun))
    assert run is not None
    assert run.trigger == str(CurationTrigger.MODEL_CHANGE)
    assert run.state == str(CurationState.QUEUED)
    assert await redis_durable.exists(f"dedup:job:reembed:{run.id}")


async def test_no_run_when_nothing_is_stale(
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
) -> None:
    """First assignment on an empty store: weights warm, but there is nothing to
    re-embed — no run, no flash of 're-indexing' in the Admin UI."""
    builtin = await get_builtin_model(db_session)
    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200

    count = await db_session.scalar(sa.select(sa.func.count()).select_from(CurationRun))
    assert count == 0


async def test_busy_platform_lock_never_fails_the_patch(
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
) -> None:
    # An unfinished run already holds the single-flight lock; stale data exists,
    # so the kick genuinely reaches the lock instead of skipping earlier.
    await _stale_chunk(db_session)
    db_session.add(CurationRun(trigger=str(CurationTrigger.MANUAL)))
    await db_session.commit()
    builtin = await get_builtin_model(db_session)

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200  # the refresh kick is best-effort
    count = await db_session.scalar(sa.select(sa.func.count()).select_from(CurationRun))
    assert count == 1  # no second run — the predicate catches up next pass


async def test_embedding_swap_during_reembed_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
) -> None:
    """An active MODEL_CHANGE run means the refresh is mid-flight — no re-pointing."""
    db_session.add(CurationRun(trigger=str(CurationTrigger.MODEL_CHANGE)))
    await db_session.commit()
    builtin = await get_builtin_model(db_session)

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 409
    assert resp.json()["code"] == "REEMBED_IN_PROGRESS"
    count = await db_session.scalar(sa.select(sa.func.count()).select_from(CurationRun))
    assert count == 1  # nothing new was journalled


async def test_other_assignments_pass_during_reembed(
    client: AsyncClient,
    db_session: AsyncSession,
    as_admin: None,
) -> None:
    """The guard covers harvester_embedding only — chat lists stay editable."""
    db_session.add(CurationRun(trigger=str(CurationTrigger.MODEL_CHANGE)))
    await db_session.commit()
    chat = await create_model(db_session, model_type="chat")

    resp = await client.patch(
        f"{BASE}/assignments",
        json={"chat_models": {"items": [{"id": chat.id, "is_enabled": True}], "default": chat.id}},
    )
    assert resp.status_code == 200
