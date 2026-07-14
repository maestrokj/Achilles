"""Identity Mapping admin: the users-by-sources matrix + manual link/unlink.

The tab lives on the Admin "Users" screen (users.html#identity-mapping); the
data lives here — KS owns identity/source_principal (acl-identity.html).
Permission is USERS_MANAGE without the member-only scope: linking a source
account is data curation, not a privilege change. A manual link (or unlink)
sets `pinned`, and auto-match never overwrites a pinned cell — the Admin's
word outlives the next sync.
"""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.pagination import OffsetPage, OffsetParams, offset_page
from achilles.api.problems import CODE_NOT_FOUND, ApiError, field_validation_error
from achilles.auth.constants import Permission
from achilles.auth.dependencies import require
from achilles.auth.models import User
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.auth.services.users_admin import user_search_clause
from achilles.db.dependencies import DbSession
from achilles.knowledge_store.models import Identity, Source, SourcePrincipal
from achilles.knowledge_store.services import identity_bridge

router = APIRouter(prefix="/admin/identity-mapping", tags=["admin-identity"])

ManageUsers = Annotated[User, require(Permission.USERS_MANAGE)]

CANDIDATES_LIMIT = 20

LINK_STATUSES = {"matched", "unmatched", "manual"}


class LinkOut(BaseModel):
    principal_id: int
    source_id: int
    source_user_id: str
    email: str | None
    display_name: str | None
    pinned: bool


class MappingRowOut(BaseModel):
    user_id: int
    full_name: str
    email: str
    links: list[LinkOut]


class MappingSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    connector_type: str


class MappingPage(OffsetPage[MappingRowOut]):
    sources: list[MappingSourceOut]


class CandidateOut(BaseModel):
    id: int
    source_user_id: str
    email: str | None
    display_name: str | None
    linked_user_id: int | None
    pinned: bool


class CandidatesOut(BaseModel):
    items: list[CandidateOut]


class LinkRequest(BaseModel):
    principal_id: int
    user_id: int


class UnlinkRequest(BaseModel):
    principal_id: int


def _link_out(principal: SourcePrincipal) -> LinkOut:
    return LinkOut(
        principal_id=principal.id,
        source_id=principal.source_id,
        source_user_id=principal.source_user_id,
        email=principal.email,
        display_name=principal.display_name,
        pinned=principal.pinned,
    )


@router.get("")
async def mapping_matrix(
    actor: ManageUsers,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    source_id: Annotated[list[int] | None, Query()] = None,
    link_status: Annotated[list[str] | None, Query()] = None,
) -> MappingPage:
    """One row per platform user.

    Cells come as flat links; the client folds them under the source columns.
    """
    del actor
    stmt = sa.select(User).order_by(User.full_name, User.id)
    if q:
        stmt = stmt.where(user_search_clause(q.strip()))

    for status in link_status or []:
        if status not in LINK_STATUSES:
            raise field_validation_error(
                "link_status",
                "matched | unmatched | manual",
                detail=f"unknown link_status {status!r}",
            )
    if link_status:
        linked = identity_bridge.linked_principals()
        if source_id:
            linked = linked.where(SourcePrincipal.source_id.in_(source_id))
        conditions: list[sa.ColumnElement[bool]] = []
        if "manual" in link_status:
            conditions.append(sa.exists(linked.where(SourcePrincipal.pinned)))
        if "matched" in link_status:
            conditions.append(sa.exists(linked.where(sa.not_(SourcePrincipal.pinned))))
        if "unmatched" in link_status:
            # At least one (selected) source where the user has no linked account.
            per_source_link = linked.where(SourcePrincipal.source_id == Source.id).correlate(
                User, Source
            )
            source_scope = sa.select(Source.id)
            if source_id:
                source_scope = source_scope.where(Source.id.in_(source_id))
            uncovered = source_scope.where(sa.not_(sa.exists(per_source_link))).correlate(User)
            conditions.append(sa.exists(uncovered))
        stmt = stmt.where(sa.or_(*conditions))

    users, total, page = await offset_page(session, stmt, params)

    links_by_user: dict[int, list[LinkOut]] = {}
    if users:
        rows = await session.execute(
            sa.select(Identity.user_id, SourcePrincipal)
            .join(SourcePrincipal, SourcePrincipal.identity_id == Identity.id)
            .where(Identity.user_id.in_([u.id for u in users]))
            .order_by(SourcePrincipal.source_id, SourcePrincipal.id)
        )
        for user_id, principal in rows.tuples():
            if user_id is None:  # pragma: no cover — filtered by the IN above
                continue
            links_by_user.setdefault(int(user_id), []).append(_link_out(principal))

    sources = (await session.scalars(sa.select(Source).order_by(Source.id))).all()
    return MappingPage(
        items=[
            MappingRowOut(
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                links=links_by_user.get(user.id, []),
            )
            for user in users
        ],
        total=total,
        page=page,
        per_page=params.per_page,
        sources=[MappingSourceOut.model_validate(s) for s in sources],
    )


@router.get("/candidates")
async def link_candidates(
    actor: ManageUsers,
    session: DbSession,
    source_id: int,
    q: str | None = None,
) -> CandidatesOut:
    """Source accounts to pick from in the link popover, likely matches first."""
    del actor
    stmt = sa.select(SourcePrincipal, Identity.user_id).outerjoin(
        Identity, Identity.id == SourcePrincipal.identity_id
    )
    stmt = stmt.where(SourcePrincipal.source_id == source_id)
    if q:
        needle = q.strip()
        stmt = stmt.where(
            sa.or_(
                SourcePrincipal.email.icontains(needle, autoescape=True),
                SourcePrincipal.display_name.icontains(needle, autoescape=True),
                SourcePrincipal.source_user_id.icontains(needle, autoescape=True),
            )
        )
    # Unlinked accounts first — those are what the popover is usually for.
    stmt = stmt.order_by(
        SourcePrincipal.identity_id.is_not(None), SourcePrincipal.display_name, SourcePrincipal.id
    ).limit(CANDIDATES_LIMIT)
    rows = await session.execute(stmt)
    return CandidatesOut(
        items=[
            CandidateOut(
                id=principal.id,
                source_user_id=principal.source_user_id,
                email=principal.email,
                display_name=principal.display_name,
                linked_user_id=int(user_id) if user_id is not None else None,
                pinned=principal.pinned,
            )
            for principal, user_id in rows.tuples()
        ]
    )


async def _identity_for_user(session: AsyncSession, user: User) -> int:
    """The user's canonical identity row, created from their platform email if absent.

    Unlike link_identity_for_user, an identity the user already holds under a
    different email is deliberately kept — the Admin links accounts to the
    person, not to the current mailbox.
    """
    identity_id = await session.scalar(sa.select(Identity.id).where(Identity.user_id == user.id))
    if identity_id is not None:
        return identity_id
    existing = await session.scalar(
        sa.select(Identity).where(sa.func.lower(Identity.email) == user.email.lower())
    )
    if existing is not None:
        existing.user_id = user.id
        await session.flush()
        return existing.id
    # No identity anywhere — the race-safe upsert creates it and auto-links back.
    return await identity_bridge.upsert_identity(
        session, email=user.email.lower(), display_name=user.full_name
    )


@router.post("/link")
async def link_principal(
    body: LinkRequest, actor: ManageUsers, request: Request, session: DbSession
) -> LinkOut:
    principal = await session.get(SourcePrincipal, body.principal_id)
    if principal is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such source account")
    user = await session.get(User, body.user_id)
    if user is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such user")

    principal.identity_id = await _identity_for_user(session, user)
    principal.pinned = True
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.IDENTITY_LINK,
        actor_id=actor.id,
        target_type="source_principal",
        target_id=str(principal.id),
        meta={"user_id": user.id, "source_id": principal.source_id},
    )
    return _link_out(principal)


@router.post("/unlink", status_code=204)
async def unlink_principal(
    body: UnlinkRequest, actor: ManageUsers, request: Request, session: DbSession
) -> None:
    """Detach and keep the pin.

    A deliberate unlink must survive the next sync's auto-match just like a
    deliberate link.
    """
    principal = await session.get(SourcePrincipal, body.principal_id)
    if principal is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such source account")
    principal.identity_id = None
    principal.pinned = True
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.IDENTITY_UNLINK,
        actor_id=actor.id,
        target_type="source_principal",
        target_id=str(principal.id),
        meta={"source_id": principal.source_id},
    )
