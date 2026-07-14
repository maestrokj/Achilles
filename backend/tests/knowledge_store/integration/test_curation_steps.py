"""Curation Pass steps: materialization, merge, decay — idempotent recomputes (P0)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import create_edge, create_entity, create_source

from achilles.knowledge_store.constants import AuthorityTier, EdgeOrigin, RelType
from achilles.knowledge_store.models import Entity, EntityEdge, EntityRef
from achilles.knowledge_store.services import curation_steps
from achilles.query_engine.models import AccessCounter

pytestmark = [pytest.mark.integration, pytest.mark.p0]

NOW = datetime(2026, 7, 1, tzinfo=UTC)


async def _edges(session: AsyncSession) -> list[tuple[int, int, str, str]]:
    rows = await session.execute(
        sa.select(
            EntityEdge.src_entity_id,
            EntityEdge.dst_entity_id,
            EntityEdge.rel_type,
            EntityEdge.origin,
        ).order_by(EntityEdge.id)
    )
    return [tuple(r) for r in rows]


# --- materialize_refs ---


async def test_ref_materializes_once_target_arrives(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    src_id = (await create_entity(db_session, source_id=source.id, source_entity_id="A-1")).id
    db_session.add(
        EntityRef(src_entity_id=src_id, relation="child_of", target_kind="page", target_ref="A-2")
    )
    await db_session.commit()

    # Target not there yet — the claim waits.
    assert await curation_steps.materialize_refs(db_session) == 0
    await db_session.commit()

    target_id = (
        await create_entity(
            db_session, source_id=source.id, source_type="page", source_entity_id="A-2"
        )
    ).id
    assert await curation_steps.materialize_refs(db_session) == 1
    await db_session.commit()

    edges = await _edges(db_session)
    assert edges == [(src_id, target_id, str(RelType.CHILD_OF), str(EdgeOrigin.CURATION))]
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(EntityRef))) == 0

    # Idempotent: a second pass changes nothing.
    assert await curation_steps.materialize_refs(db_session) == 0


async def test_ambiguous_ref_waits(db_session: AsyncSession) -> None:
    first = await create_source(db_session)
    second = await create_source(db_session)
    src_id = (await create_entity(db_session, source_id=first.id)).id
    await create_entity(db_session, source_id=first.id, source_type="page", source_entity_id="DUP")
    await create_entity(db_session, source_id=second.id, source_type="page", source_entity_id="DUP")
    db_session.add(
        EntityRef(src_entity_id=src_id, relation="mentions", target_kind="page", target_ref="DUP")
    )
    await db_session.commit()

    assert await curation_steps.materialize_refs(db_session) == 0  # two matches → wait
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(EntityRef))) == 1


async def test_source_hint_narrows_the_match(db_session: AsyncSession) -> None:
    jira = await create_source(db_session, connector_type="jira")
    gitlab = await create_source(db_session, connector_type="gitlab")
    src_id = (await create_entity(db_session, source_id=gitlab.id)).id
    jira_target_id = (
        await create_entity(
            db_session, source_id=jira.id, source_type="issue", source_entity_id="KEY-1"
        )
    ).id
    await create_entity(
        db_session, source_id=gitlab.id, source_type="issue", source_entity_id="KEY-1"
    )
    db_session.add(
        EntityRef(
            src_entity_id=src_id,
            relation="mentions",
            target_kind="issue",
            target_ref="KEY-1",
            source_hint="jira",
        )
    )
    await db_session.commit()

    assert await curation_steps.materialize_refs(db_session) == 1
    edges = await _edges(db_session)
    assert edges[0][1] == jira_target_id  # the hint picked the jira twin


# --- merge_duplicates ---


async def test_merge_collapses_cross_source_twins(db_session: AsyncSession) -> None:
    low = await create_source(db_session, authority_tier=str(AuthorityTier.LOW))
    high = await create_source(db_session, authority_tier=str(AuthorityTier.HIGH))
    loser = await create_entity(
        db_session, source_id=low.id, content_hash="same", source_updated_at=NOW
    )
    loser_id = loser.id
    winner = await create_entity(
        db_session, source_id=high.id, content_hash="same", source_updated_at=NOW
    )
    winner_id = winner.id
    other = await create_entity(db_session, source_id=low.id, content_hash="different")
    other_id = other.id
    await create_edge(db_session, src_entity_id=other_id, dst_entity_id=loser_id)

    merged = await curation_steps.merge_duplicates(db_session)
    await db_session.commit()

    assert merged == 1
    db_session.expire_all()
    hidden = await db_session.get(Entity, loser_id)
    assert hidden is not None
    assert hidden.is_deleted is True
    kept = await db_session.get(Entity, winner_id)
    assert kept is not None
    assert kept.is_deleted is False

    edges = await _edges(db_session)
    # other → loser became other → winner; loser marked duplicate_of winner.
    assert (other_id, winner_id, str(RelType.LINKS_TO), str(EdgeOrigin.HARVESTER)) in edges
    assert (loser_id, winner_id, str(RelType.DUPLICATE_OF), str(EdgeOrigin.CURATION)) in edges
    assert not any(e[1] == loser_id and e[0] == other_id for e in edges)

    # Idempotent: nothing left to merge.
    assert await curation_steps.merge_duplicates(db_session) == 0


async def test_same_source_twins_are_not_merged(db_session: AsyncSession) -> None:
    source = await create_source(db_session)
    await create_entity(db_session, source_id=source.id, content_hash="same")
    await create_entity(db_session, source_id=source.id, content_hash="same")

    assert await curation_steps.merge_duplicates(db_session) == 0


# --- trust_decay ---


async def test_decay_orders_by_authority_freshness_demand(db_session: AsyncSession) -> None:
    low = await create_source(db_session, authority_tier=str(AuthorityTier.LOW))
    high = await create_source(db_session, authority_tier=str(AuthorityTier.HIGH))
    now = datetime.now(UTC)

    fresh_high = (await create_entity(db_session, source_id=high.id, source_updated_at=now)).id
    fresh_low = (await create_entity(db_session, source_id=low.id, source_updated_at=now)).id
    stale_high = (
        await create_entity(
            db_session, source_id=high.id, source_updated_at=now - timedelta(days=365)
        )
    ).id
    demanded_low = (await create_entity(db_session, source_id=low.id, source_updated_at=now)).id
    db_session.add(AccessCounter(entity_ref=demanded_low, hits=100, last_accessed_at=now))
    await db_session.commit()

    rescored = await curation_steps.trust_decay(db_session)
    await db_session.commit()
    assert rescored == 4

    db_session.expire_all()

    async def score(entity_id: int) -> float:
        value = await db_session.scalar(sa.select(Entity.trust_score).where(Entity.id == entity_id))
        assert value is not None
        return value

    assert await score(fresh_high) > await score(fresh_low)  # authority
    assert await score(fresh_high) > await score(stale_high)  # freshness
    assert await score(demanded_low) > await score(fresh_low)  # demand

    # Idempotent: the recompute converges.
    first = await score(fresh_high)
    await curation_steps.trust_decay(db_session)
    await db_session.commit()
    db_session.expire_all()
    assert await score(fresh_high) == pytest.approx(first, rel=1e-3)
