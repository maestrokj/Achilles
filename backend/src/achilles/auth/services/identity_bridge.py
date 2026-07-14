"""Bridge between accounts and Knowledge Store content identities.

Knowledge Store owns the `identity` table and the link operation; auto-link runs
on exact lower(email) match at invite-accept, setup and admin email-change
(acl-identity.html#identity). This module is the Auth-side delegate.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.services import identity_bridge


async def auto_link_identity(session: AsyncSession, *, user_id: int, email: str) -> None:
    await identity_bridge.link_identity_for_user(session, user_id=user_id, email=email)
