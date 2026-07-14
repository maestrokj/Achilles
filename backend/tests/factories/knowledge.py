"""Knowledge Store factory helpers: sources, the ACL five, entities and projections."""

import itertools
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.models import User
from achilles.knowledge_store.constants import AclScope, EdgeOrigin, RelType
from achilles.knowledge_store.models import (
    Chunk,
    Entity,
    EntityAcl,
    EntityEdge,
    GroupMembership,
    Identity,
    Source,
    SourceGroup,
    SourcePrincipal,
)

_seq = itertools.count(1)


async def create_source(session: AsyncSession, **kwargs: object) -> Source:
    n = next(_seq)
    source = Source(**{"name": f"Source {n}", "connector_type": "jira", **kwargs})  # type: ignore[arg-type]
    session.add(source)
    await session.commit()
    return source


async def create_identity(
    session: AsyncSession,
    *,
    email: str | None = None,
    display_name: str | None = None,
    user_id: int | None = None,
) -> Identity:
    n = next(_seq)
    identity = Identity(
        email=email or f"person{n}@example.com", display_name=display_name, user_id=user_id
    )
    session.add(identity)
    await session.commit()
    return identity


async def create_principal(
    session: AsyncSession,
    *,
    source_id: int,
    identity_id: int | None = None,
    email: str | None = None,
    source_user_id: str | None = None,
) -> SourcePrincipal:
    n = next(_seq)
    principal = SourcePrincipal(
        source_id=source_id,
        source_user_id=source_user_id or f"native-{n}",
        email=email,
        identity_id=identity_id,
    )
    session.add(principal)
    await session.commit()
    return principal


async def create_group(
    session: AsyncSession, *, source_id: int, name: str | None = None
) -> SourceGroup:
    n = next(_seq)
    group = SourceGroup(
        source_id=source_id, source_group_id=f"container-{n}", name=name or f"Group {n}"
    )
    session.add(group)
    await session.commit()
    return group


async def add_membership(session: AsyncSession, *, group_id: int, principal_id: int) -> None:
    session.add(GroupMembership(source_group_id=group_id, source_principal_id=principal_id))
    await session.commit()


async def create_entity(session: AsyncSession, *, source_id: int, **kwargs: object) -> Entity:
    n = next(_seq)
    entity = Entity(
        **{  # type: ignore[arg-type]
            "source_id": source_id,
            "source_type": "page",
            "source_entity_id": f"native-entity-{n}",
            "title": f"Entity {n}",
            **kwargs,
        }
    )
    session.add(entity)
    await session.commit()
    return entity


async def create_chunk(
    session: AsyncSession,
    *,
    entity_id: int,
    ordinal: int = 0,
    text: str = "chunk text",
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
) -> Chunk:
    chunk = Chunk(
        entity_id=entity_id,
        ordinal=ordinal,
        text=text,
        embedding=embedding,
        embedding_model=embedding_model,
    )
    session.add(chunk)
    await session.commit()
    return chunk


async def create_edge(
    session: AsyncSession,
    *,
    src_entity_id: int,
    dst_entity_id: int,
    rel_type: str = RelType.LINKS_TO.value,
    weight: float | None = None,
    origin: str = EdgeOrigin.HARVESTER.value,
) -> EntityEdge:
    edge = EntityEdge(
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        rel_type=rel_type,
        weight=weight,
        origin=origin,
    )
    session.add(edge)
    await session.commit()
    return edge


async def grant(
    session: AsyncSession,
    *,
    entity_id: int,
    scope: str = AclScope.PUBLIC.value,
    source_group_id: int | None = None,
    source_principal_id: int | None = None,
) -> EntityAcl:
    acl = EntityAcl(
        entity_id=entity_id,
        scope=scope,
        source_group_id=source_group_id,
        source_principal_id=source_principal_id,
    )
    session.add(acl)
    await session.commit()
    return acl


@dataclass(frozen=True, slots=True)
class AclScene:
    """The ACL five wired to one platform user: users → identity → principal → group."""

    source: Source
    identity: Identity
    principal: SourcePrincipal
    group: SourceGroup


async def acl_scene(session: AsyncSession, *, user: User) -> AclScene:
    source = await create_source(session)
    identity = await create_identity(session, email=user.email, user_id=user.id)
    principal = await create_principal(session, source_id=source.id, identity_id=identity.id)
    group = await create_group(session, source_id=source.id)
    await add_membership(session, group_id=group.id, principal_id=principal.id)
    return AclScene(source=source, identity=identity, principal=principal, group=group)
