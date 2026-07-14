"""Entity upsert: idempotence by the natural key, projection sync, FK semantics (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import create_entity, create_principal, create_source

from achilles.knowledge_store.constants import AclScope, RelType
from achilles.knowledge_store.models import (
    Chunk,
    Entity,
    EntityAcl,
    EntityEdge,
    EntityRef,
    Source,
    SourcePrincipal,
)
from achilles.knowledge_store.services.entities import (
    AclDraft,
    EdgeDraft,
    EntityPayload,
    RefDraft,
    upsert_entity,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]

PARAGRAPH_A = " ".join(f"alpha{i}" for i in range(300))
PARAGRAPH_B = " ".join(f"beta{i}" for i in range(300))


def payload(source_id: int, **overrides: object) -> EntityPayload:
    defaults: dict[str, object] = {
        "source_id": source_id,
        "source_type": "ticket",
        "source_entity_id": "T-1",
        "title": "Ticket one",
        "body": f"{PARAGRAPH_A}\n\n{PARAGRAPH_B}",
    }
    return EntityPayload(**{**defaults, **overrides})  # type: ignore[arg-type]


async def test_upsert_is_idempotent_by_natural_key(db_session: AsyncSession):
    source = await create_source(db_session)
    first = await upsert_entity(db_session, payload(source.id))
    await db_session.commit()
    second = await upsert_entity(db_session, payload(source.id))
    await db_session.commit()

    assert first == second
    count = await db_session.scalar(sa.select(sa.func.count(Entity.id)))
    assert count == 1


async def test_unchanged_fragments_are_not_touched(db_session: AsyncSession):
    source = await create_source(db_session)
    entity_id = await upsert_entity(db_session, payload(source.id))
    await db_session.commit()
    before = dict(
        (
            await db_session.execute(
                sa.select(Chunk.ordinal, Chunk.id).where(Chunk.entity_id == entity_id)
            )
        )
        .tuples()
        .all()
    )

    changed = payload(source.id, body=f"{PARAGRAPH_A}\n\n{PARAGRAPH_B} changed")
    await upsert_entity(db_session, changed)
    await db_session.commit()

    after = dict(
        (
            await db_session.execute(
                sa.select(Chunk.ordinal, Chunk.id).where(Chunk.entity_id == entity_id)
            )
        )
        .tuples()
        .all()
    )
    assert after[0] == before[0], "untouched fragment keeps its row"
    assert after[1] == before[1], "changed fragment is updated in place, not recreated"
    texts = dict(
        (
            await db_session.execute(
                sa.select(Chunk.ordinal, Chunk.text).where(Chunk.entity_id == entity_id)
            )
        )
        .tuples()
        .all()
    )
    assert texts[1].endswith("changed")


async def test_surplus_chunks_are_deleted_when_body_shrinks(db_session: AsyncSession):
    source = await create_source(db_session)
    entity_id = await upsert_entity(db_session, payload(source.id))
    await db_session.commit()

    await upsert_entity(db_session, payload(source.id, body=PARAGRAPH_A))
    await db_session.commit()

    count = await db_session.scalar(
        sa.select(sa.func.count(Chunk.id)).where(Chunk.entity_id == entity_id)
    )
    assert count == 1


async def test_projections_land_in_one_call(db_session: AsyncSession):
    source = await create_source(db_session)
    other_id = (await create_entity(db_session, source_id=source.id)).id
    principal = await create_principal(db_session, source_id=source.id)

    entity_id = await upsert_entity(
        db_session,
        payload(
            source.id,
            edges=(EdgeDraft(dst_entity_id=other_id, rel_type=RelType.LINKS_TO.value),),
            refs=(RefDraft(relation="mentions", target_kind="issue", target_ref="J-42"),),
            acl=(AclDraft(scope=AclScope.PRINCIPAL.value, source_principal_id=principal.id),),
        ),
    )
    await db_session.commit()

    edge = (await db_session.execute(sa.select(EntityEdge))).scalar_one()
    assert (edge.src_entity_id, edge.dst_entity_id) == (entity_id, other_id)
    ref = (await db_session.execute(sa.select(EntityRef))).scalar_one()
    assert (ref.target_kind, ref.target_ref) == ("issue", "J-42")
    acl = (await db_session.execute(sa.select(EntityAcl))).scalar_one()
    assert acl.source_principal_id == principal.id


async def test_acl_replacement_lands_revocation(db_session: AsyncSession):
    source = await create_source(db_session)
    principal = await create_principal(db_session, source_id=source.id)
    grants = (AclDraft(scope=AclScope.PRINCIPAL.value, source_principal_id=principal.id),)
    await upsert_entity(db_session, payload(source.id, acl=grants))
    await db_session.commit()

    await upsert_entity(db_session, payload(source.id, acl=(AclDraft(scope="public"),)))
    await db_session.commit()

    acl = (await db_session.execute(sa.select(EntityAcl))).scalar_one()
    assert acl.scope == "public"
    assert acl.source_principal_id is None


async def test_upsert_revives_a_soft_deleted_entity(db_session: AsyncSession):
    source = await create_source(db_session)
    entity_id = await upsert_entity(db_session, payload(source.id))
    await db_session.execute(
        sa.update(Entity).where(Entity.id == entity_id).values(is_deleted=True)
    )
    await db_session.commit()

    await upsert_entity(db_session, payload(source.id))
    await db_session.commit()

    entity = await db_session.get(Entity, entity_id)
    assert entity is not None
    assert entity.is_deleted is False
    assert entity.deleted_at is None


async def test_author_delete_nulls_source_delete_cascades(db_session: AsyncSession):
    source = await create_source(db_session)
    principal = await create_principal(db_session, source_id=source.id)
    entity_id = await upsert_entity(
        db_session, payload(source.id, author_principal_id=principal.id)
    )
    await db_session.commit()

    await db_session.execute(sa.delete(SourcePrincipal).where(SourcePrincipal.id == principal.id))
    await db_session.commit()
    entity = await db_session.get(Entity, entity_id)
    assert entity is not None
    assert entity.author_principal_id is None  # ON DELETE SET NULL

    await db_session.execute(sa.delete(Source).where(Source.id == source.id))
    await db_session.commit()
    db_session.expire_all()  # bypass the identity map, re-read from the DB
    assert await db_session.get(Entity, entity_id) is None  # ON DELETE CASCADE
    chunk_count = await db_session.scalar(sa.select(sa.func.count(Chunk.id)))
    assert chunk_count == 0


async def test_status_check_rejects_unknown_values(db_session: AsyncSession):
    source = await create_source(db_session)
    with pytest.raises(IntegrityError):
        await upsert_entity(db_session, payload(source.id, status="draught"))
    await db_session.rollback()
