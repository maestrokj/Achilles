"""Personal feed routes: list · unread · read / read-all · prefs (index.html#api).

One's own feed only: targeted rows are one's own, broadcast rows exist as
materialized deliveries for whoever wore the role at event time. A foreign
notification is an invisible 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from achilles.api.pagination import OffsetPage, OffsetParams, offset_window
from achilles.auth.dependencies import CurrentUser
from achilles.db.dependencies import DbSession
from achilles.email.service import reader_locale
from achilles.notifications import service
from achilles.notifications.schemas import (
    NotificationOut,
    Prefs,
    UnreadOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    user: CurrentUser,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    event_type: Annotated[list[str] | None, Query(alias="type")] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    unread: bool = False,  # noqa: FBT001, FBT002 — a query-string facet, not a code trap
    period: str | None = None,
    q: str | None = None,
) -> OffsetPage[NotificationOut]:
    """The inbox: search plus facets by type/severity/unread/period, series shown as xN."""
    locale = await reader_locale(session, user)
    stmt = service.feed_stmt(
        user,
        event_types=event_type,
        severities=severity,
        unread_only=unread,
        period=period,
        q=q,
    )
    total, page = await offset_window(session, stmt, params)
    rows = await session.execute(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return OffsetPage(
        items=[
            service.notification_out(notification, delivery, locale=locale)
            for notification, delivery in rows
        ],
        total=total,
        page=page,
        per_page=params.per_page,
    )


@router.get("/unread")
async def unread(user: CurrentUser, session: DbSession) -> UnreadOut:
    """The bell counter — cheap enough for the background poll."""
    return UnreadOut(count=await service.unread_count(session, user.id))


@router.get("/prefs")
async def get_prefs(user: CurrentUser, session: DbSession) -> Prefs:
    """Effective values: a stored row or the catalog default per category."""
    return Prefs(items=await service.effective_prefs(session, user))


@router.put("/prefs")
async def put_prefs(user: CurrentUser, session: DbSession, body: Prefs) -> Prefs:
    return Prefs(items=await service.put_prefs(session, user, body))


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(user: CurrentUser, session: DbSession) -> None:
    await service.read_all(session, user)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: int, user: CurrentUser, session: DbSession) -> None:
    await service.mark_read(session, user, notification_id)
