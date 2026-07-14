"""GET /admin/knowledge/metrics: the storage tiles + the per-source cut."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import create_chunk, create_edge, create_entity, create_source
from tests.factories.users import create_user

from achilles.knowledge_store.models import EntityRef

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/knowledge/metrics"


async def _seed_two_sources(session: AsyncSession) -> tuple[int, int]:
    """Source A: 2 entities, 1 chunk (embedded), 1 edge, 1 pending ref. Source B: 1 entity."""
    a = await create_source(session, name="Source A")
    b = await create_source(session, name="Source B")
    a1 = await create_entity(session, source_id=a.id)
    a2 = await create_entity(session, source_id=a.id)
    await create_entity(session, source_id=b.id)
    await create_chunk(session, entity_id=a1.id, embedding=[0.1] * 1024, embedding_model="m")
    await create_edge(session, src_entity_id=a1.id, dst_entity_id=a2.id)
    session.add(
        EntityRef(src_entity_id=a1.id, relation="mentions", target_kind="issue", target_ref="X-1")
    )
    await session.commit()
    return a.id, b.id


async def test_totals_and_per_source_cut(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    a_id, b_id = await _seed_two_sources(db_session)
    await authorize(admin.email)

    total = (await client.get(URL)).json()
    assert (total["entities"], total["chunks"], total["edges"], total["pending_refs"]) == (
        3,
        1,
        1,
        1,
    )
    assert total["vector_bytes"] > 0  # one embedded halfvec(1024)

    only_a = (await client.get(URL, params={"source_id": a_id})).json()
    assert (only_a["entities"], only_a["chunks"], only_a["edges"], only_a["pending_refs"]) == (
        2,
        1,
        1,
        1,
    )
    assert only_a["vector_bytes"] == total["vector_bytes"]

    only_b = (await client.get(URL, params={"source_id": b_id})).json()
    assert (only_b["entities"], only_b["chunks"], only_b["edges"], only_b["pending_refs"]) == (
        1,
        0,
        0,
        0,
    )
    assert only_b["vector_bytes"] == 0


async def test_deleted_chunks_do_not_count(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    source = await create_source(db_session)
    entity = await create_entity(db_session, source_id=source.id)
    chunk = await create_chunk(db_session, entity_id=entity.id, embedding=[0.1] * 1024)
    await db_session.execute(
        sa.text("UPDATE chunks SET is_deleted = true WHERE id = :id"), {"id": chunk.id}
    )
    await db_session.commit()
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    assert body["chunks"] == 0
    assert body["vector_bytes"] == 0


async def test_unknown_source_404_and_member_403(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    member = await create_user(db_session)

    await authorize(admin.email)
    assert (await client.get(URL, params={"source_id": 9999})).status_code == 404

    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403
