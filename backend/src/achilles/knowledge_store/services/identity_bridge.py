"""KS-owned identity↔users bridge (acl-identity.html#identity).

Auto-link on exact lower(email) match, idempotent and transactional. Called
synchronously from Auth (invite-accept, setup, admin email-change) and from
Harvester identity upserts (stage 5).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import User
from achilles.knowledge_store.models import Identity, SourcePrincipal


def linked_principals() -> sa.Select[tuple[int]]:
    """Principals linked to the row's user — correlated against the outer User.id.

    The one "user has a linked source account" predicate: identity-mapping
    filters wrap it in EXISTS per status, the dashboard counts users without it.
    """
    return (
        sa.select(SourcePrincipal.id)
        .join(Identity, Identity.id == SourcePrincipal.identity_id)
        .where(Identity.user_id == User.id)
        .correlate(User)
    )


async def link_identity_for_user(session: AsyncSession, *, user_id: int, email: str) -> bool:
    """Link the identity of `email` to a platform account; no match → no-op.

    The Auth-side entry point: a platform account appeared (or changed email) and
    asks KS to link the identity of the same email. The bridge follows the
    user's current email: an identity linked to them under a different email is
    unlinked first — otherwise the partial UNIQUE (user_id) would reject the
    re-link. Repeat calls are no-ops via the WHERE guards.
    """
    await session.execute(
        sa.update(Identity)
        .where(
            Identity.user_id == user_id,
            sa.func.lower(Identity.email) != sa.func.lower(email),
        )
        .values(user_id=None)
    )
    result = await session.execute(
        sa.update(Identity)
        .where(
            sa.func.lower(Identity.email) == sa.func.lower(email),
            Identity.user_id.is_(None),
        )
        .values(user_id=user_id)
    )
    return bool(getattr(result, "rowcount", 0))


async def upsert_identity(
    session: AsyncSession, *, email: str, display_name: str | None = None
) -> int:
    """Upsert a canonical person by lower(email) and auto-link back to users; returns id.

    The Harvester-side entry point (stage 5): a person observed in a source. The
    reverse auto-link covers "account existed before the content arrived".
    """
    stmt = pg_insert(Identity).values(email=email, display_name=display_name)
    identity_id = (
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[sa.text("lower(email)")],
                set_={
                    "display_name": sa.func.coalesce(
                        stmt.excluded.display_name, Identity.display_name
                    )
                },
            ).returning(Identity.id)
        )
    ).scalar_one()

    user_id = await session.scalar(
        sa.select(User.id).where(sa.func.lower(User.email) == sa.func.lower(email))
    )
    if user_id is not None:
        # One linking semantics for both directions — including the unlink of a
        # stale identity the user was linked to under a previous email.
        await link_identity_for_user(session, user_id=user_id, email=email)
    return identity_id
