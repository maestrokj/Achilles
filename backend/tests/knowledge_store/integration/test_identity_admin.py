"""Identity Mapping admin: matrix, candidates, manual link/unlink + the pin."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.knowledge import create_identity, create_principal, create_source
from tests.factories.users import create_user

from achilles.harvester.connectors.base import PrincipalDraft
from achilles.harvester.services.principals import upsert_principal
from achilles.knowledge_store.models import SourcePrincipal

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/identity-mapping"


async def test_matrix_folds_links_under_users(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, full_name="Anna Orlova", email="anna@example.com")
    source = await create_source(db_session)
    identity = await create_identity(db_session, email="anna@example.com", user_id=person.id)
    await create_principal(
        db_session, source_id=source.id, identity_id=identity.id, email="anna@example.com"
    )
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    assert [s["id"] for s in body["sources"]] == [source.id]
    anna = next(row for row in body["items"] if row["user_id"] == person.id)
    assert len(anna["links"]) == 1
    assert anna["links"][0]["source_id"] == source.id
    assert anna["links"][0]["pinned"] is False


async def test_link_status_facets(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    linked = await create_user(db_session, email="linked@example.com")
    unlinked = await create_user(db_session, email="unlinked@example.com")
    source = await create_source(db_session)
    identity = await create_identity(db_session, email="linked@example.com", user_id=linked.id)
    await create_principal(db_session, source_id=source.id, identity_id=identity.id)
    await authorize(admin.email)

    matched = (await client.get(URL, params={"link_status": "matched"})).json()
    assert {row["user_id"] for row in matched["items"]} == {linked.id}

    unmatched = (await client.get(URL, params={"link_status": "unmatched"})).json()
    ids = {row["user_id"] for row in unmatched["items"]}
    assert unlinked.id in ids and linked.id not in ids

    assert (await client.get(URL, params={"link_status": "nonsense"})).status_code == 422


async def test_manual_link_pins_and_survives_auto_match(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, email="dmitry@example.com")
    person_id = person.id
    source = await create_source(db_session)
    source_id = source.id
    # The source presents the account under a different email — auto-match failed.
    principal = await create_principal(
        db_session, source_id=source_id, email="d.sokolov@old.example.com"
    )
    principal_id = principal.id
    native_id = principal.source_user_id
    await authorize(admin.email)

    linked = await client.post(
        f"{URL}/link", json={"principal_id": principal_id, "user_id": person_id}
    )
    assert linked.status_code == 200
    assert linked.json()["pinned"] is True

    # The next sync re-observes the account with a resolvable email — the pin holds.
    await upsert_principal(
        db_session,
        source_id=source_id,
        draft=PrincipalDraft(
            source_user_id=native_id, email="someoneelse@example.com", display_name=None
        ),
    )
    await db_session.commit()
    db_session.expire_all()
    row = await db_session.get_one(SourcePrincipal, principal_id)
    linked_user = await db_session.scalar(
        sa.text("SELECT user_id FROM identity WHERE id = :iid").bindparams(iid=row.identity_id)
    )
    assert linked_user == person_id, "auto-match must not overwrite the Admin's pin"


async def test_unlink_keeps_the_pin(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, email="maria@example.com")
    source = await create_source(db_session)
    identity = await create_identity(db_session, email="maria@example.com", user_id=person.id)
    principal = await create_principal(
        db_session, source_id=source.id, identity_id=identity.id, email="maria@example.com"
    )
    principal_id = principal.id
    source_user_id = principal.source_user_id
    await authorize(admin.email)

    resp = await client.post(f"{URL}/unlink", json={"principal_id": principal_id})
    assert resp.status_code == 204

    # The next sync resolves the same email again — the deliberate unlink holds.
    await upsert_principal(
        db_session,
        source_id=source.id,
        draft=PrincipalDraft(
            source_user_id=source_user_id, email="maria@example.com", display_name=None
        ),
    )
    await db_session.commit()
    db_session.expire_all()
    row = await db_session.get_one(SourcePrincipal, principal_id)
    assert row.identity_id is None
    assert row.pinned is True


async def test_candidates_prefer_unlinked(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    source = await create_source(db_session)
    identity = await create_identity(db_session)
    await create_principal(db_session, source_id=source.id, identity_id=identity.id)
    free = await create_principal(db_session, source_id=source.id)
    await authorize(admin.email)

    body = (await client.get(f"{URL}/candidates", params={"source_id": source.id})).json()
    assert body["items"][0]["id"] == free.id, "unlinked accounts come first"
    assert body["items"][0]["linked_user_id"] is None


async def test_member_is_403(client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403
