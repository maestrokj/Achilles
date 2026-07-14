"""identity ↔ users auto-link by exact lower(email): both directions, 1:1 (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories.knowledge import create_identity
from tests.factories.users import create_user

from achilles.auth.services.identity_bridge import auto_link_identity
from achilles.knowledge_store.models import Identity
from achilles.knowledge_store.services.identity_bridge import (
    link_identity_for_user,
    upsert_identity,
)

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def test_account_appears_after_content_links_forward(db_session: AsyncSession):
    """Direction 1: the identity existed (harvested), then the account is created."""
    identity = await create_identity(db_session, email="alice@corp.test")
    user = await create_user(db_session, email="alice@corp.test")

    linked = await link_identity_for_user(db_session, user_id=user.id, email=user.email)
    await db_session.commit()

    assert linked is True
    await db_session.refresh(identity)
    assert identity.user_id == user.id


async def test_content_appears_after_account_links_backward(db_session: AsyncSession):
    """Direction 2: the account existed, then the person is observed in a source."""
    user = await create_user(db_session, email="bob@corp.test")

    identity_id = await upsert_identity(db_session, email="bob@corp.test", display_name="Bob")
    await db_session.commit()

    identity = await db_session.get(Identity, identity_id)
    assert identity is not None
    assert identity.user_id == user.id


async def test_case_is_normalized_both_ways(db_session: AsyncSession):
    identity = await create_identity(db_session, email="ALICE@CORP.test")
    user = await create_user(db_session, email="alice@corp.test")

    await link_identity_for_user(db_session, user_id=user.id, email=user.email)
    await db_session.commit()
    await db_session.refresh(identity)
    assert identity.user_id == user.id

    # And the upsert merges by lower(email), not by the literal string.
    same_id = await upsert_identity(db_session, email="alice@CORP.TEST")
    assert same_id == identity.id
    count = await db_session.scalar(sa.select(sa.func.count(Identity.id)))
    assert count == 1


async def test_no_match_stays_unlinked(db_session: AsyncSession):
    identity = await create_identity(db_session, email="contractor@vendor.test")
    user = await create_user(db_session, email="employee@corp.test")

    linked = await link_identity_for_user(db_session, user_id=user.id, email=user.email)
    await db_session.commit()

    assert linked is False
    await db_session.refresh(identity)
    assert identity.user_id is None  # awaits manual mapping in Admin


async def test_link_is_idempotent(db_session: AsyncSession):
    await create_identity(db_session, email="carol@corp.test")
    user = await create_user(db_session, email="carol@corp.test")

    first = await link_identity_for_user(db_session, user_id=user.id, email=user.email)
    second = await link_identity_for_user(db_session, user_id=user.id, email=user.email)
    await db_session.commit()
    assert (first, second) == (True, False)  # repeat is a no-op, not an error


async def test_email_change_moves_the_bridge(db_session: AsyncSession):
    """Admin changes the user's email: the bridge follows it — the identity of
    the old email unlinks, the identity of the new one links, no IntegrityError."""
    user = await create_user(db_session, email="old@corp.test")
    old = await create_identity(db_session, email="old@corp.test", user_id=user.id)
    new = await create_identity(db_session, email="new@corp.test")

    linked = await link_identity_for_user(db_session, user_id=user.id, email="new@corp.test")
    await db_session.commit()

    assert linked is True
    await db_session.refresh(old)
    await db_session.refresh(new)
    assert old.user_id is None
    assert new.user_id == user.id


async def test_upsert_relinks_a_user_left_on_a_stale_identity(db_session: AsyncSession):
    """Harvester direction of the same move: the observed email matches the
    user's current one while they are still linked to a previous-email identity."""
    user = await create_user(db_session, email="fresh@corp.test")
    stale = await create_identity(db_session, email="stale@corp.test", user_id=user.id)

    identity_id = await upsert_identity(db_session, email="fresh@corp.test")
    await db_session.commit()

    identity = await db_session.get(Identity, identity_id)
    assert identity is not None
    assert identity.user_id == user.id
    await db_session.refresh(stale)
    assert stale.user_id is None


async def test_bridge_is_one_to_one(db_session: AsyncSession):
    user = await create_user(db_session, email="dave@corp.test")
    await create_identity(db_session, email="dave@corp.test", user_id=user.id)

    with pytest.raises(IntegrityError):  # partial UNIQUE (user_id)
        await create_identity(db_session, email="dave.alias@corp.test", user_id=user.id)
    await db_session.rollback()


async def test_auth_delegate_calls_the_ks_owned_link(db_session: AsyncSession):
    """The Auth-side seam is closed: auto_link_identity is the KS op, not a no-op."""
    identity = await create_identity(db_session, email="eve@corp.test")
    user = await create_user(db_session, email="eve@corp.test")

    await auto_link_identity(db_session, user_id=user.id, email=user.email)
    await db_session.commit()

    await db_session.refresh(identity)
    assert identity.user_id == user.id


async def test_display_name_is_enriched_not_erased(db_session: AsyncSession):
    identity_id = await upsert_identity(db_session, email="fay@corp.test", display_name="Fay")
    await upsert_identity(db_session, email="fay@corp.test", display_name=None)
    await db_session.commit()

    identity = await db_session.get(Identity, identity_id)
    assert identity is not None
    assert identity.display_name == "Fay"  # a sparse source must not erase the name
