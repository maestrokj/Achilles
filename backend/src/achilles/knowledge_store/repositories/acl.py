"""ACL grant statements (acl-identity.html#entity-acl).

Grants are a snapshot with no payload — the sync replaces the set wholesale, which
is how revocation lands (rights are eventually consistent, next sync applies them).
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import EntityAcl

if TYPE_CHECKING:
    from achilles.knowledge_store.services.entities import AclDraft


async def replace_grants(session: AsyncSession, entity_id: int, grants: Iterable[AclDraft]) -> None:
    await session.execute(sa.delete(EntityAcl).where(EntityAcl.entity_id == entity_id))
    session.add_all(
        EntityAcl(
            entity_id=entity_id,
            scope=g.scope,
            source_group_id=g.source_group_id,
            source_principal_id=g.source_principal_id,
        )
        for g in grants
    )
    await session.flush()
