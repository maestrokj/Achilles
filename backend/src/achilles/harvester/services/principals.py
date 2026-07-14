"""ACL/identity ingest: principals, groups, membership snapshots (acl-identity.html).

Harvester produces, KS stores: source_principal/source_group upsert by natural
key, membership replaced wholesale (flat snapshot v1 — how revocation lands).
A principal with an email is bridged into `identity` on the spot.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.harvester.connectors.base import GroupDraft, PrincipalDraft
from achilles.knowledge_store.models import GroupMembership, SourceGroup, SourcePrincipal
from achilles.knowledge_store.services.identity_bridge import upsert_identity


async def upsert_principal(session: AsyncSession, *, source_id: int, draft: PrincipalDraft) -> int:
    """Upsert by (source_id, source_user_id); email → identity bridge. Returns pk."""
    identity_id = None
    if draft.email:
        identity_id = await upsert_identity(
            session, email=draft.email, display_name=draft.display_name
        )
    stmt = pg_insert(SourcePrincipal).values(
        source_id=source_id,
        source_user_id=draft.source_user_id,
        email=draft.email,
        display_name=draft.display_name,
        identity_id=identity_id,
    )
    return (
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_source_principal_native",
                set_={
                    "email": sa.func.coalesce(stmt.excluded.email, SourcePrincipal.email),
                    "display_name": sa.func.coalesce(
                        stmt.excluded.display_name, SourcePrincipal.display_name
                    ),
                    # A pinned link is the Admin's word — auto-match never overwrites it.
                    "identity_id": sa.case(
                        (SourcePrincipal.pinned, SourcePrincipal.identity_id),
                        else_=sa.func.coalesce(
                            stmt.excluded.identity_id, SourcePrincipal.identity_id
                        ),
                    ),
                },
            ).returning(SourcePrincipal.id)
        )
    ).scalar_one()


async def upsert_group(
    session: AsyncSession, *, source_id: int, source_group_id: str, name: str, kind: str | None
) -> int:
    """Upsert by (source_id, source_group_id); returns pk."""
    stmt = pg_insert(SourceGroup).values(
        source_id=source_id, source_group_id=source_group_id, name=name, kind=kind
    )
    return (
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_source_group_native",
                set_={"name": stmt.excluded.name, "kind": stmt.excluded.kind},
            ).returning(SourceGroup.id)
        )
    ).scalar_one()


async def replace_membership(
    session: AsyncSession, *, group_pk: int, principal_pks: set[int]
) -> None:
    """Wholesale membership snapshot — additions and revocations land together."""
    await session.execute(
        sa.delete(GroupMembership).where(GroupMembership.source_group_id == group_pk)
    )
    if principal_pks:
        await session.execute(
            pg_insert(GroupMembership)
            .values(
                [
                    {"source_group_id": group_pk, "source_principal_id": pk}
                    for pk in sorted(principal_pks)
                ]
            )
            .on_conflict_do_nothing()
        )


async def ingest_group(
    session: AsyncSession,
    *,
    source_id: int,
    draft: GroupDraft,
    principal_pk_by_native: dict[str, int],
) -> int:
    """Upsert the group and replace its membership from already-known principals."""
    group_pk = await upsert_group(
        session,
        source_id=source_id,
        source_group_id=draft.source_group_id,
        name=draft.name,
        kind=draft.kind,
    )
    member_pks = {
        principal_pk_by_native[native]
        for native in draft.member_source_user_ids
        if native in principal_pk_by_native
    }
    await replace_membership(session, group_pk=group_pk, principal_pks=member_pks)
    return group_pk
