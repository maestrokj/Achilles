"""Feed queries, read state, personal prefs and the admin channel/route config."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_CONFLICT, CODE_NOT_FOUND, ApiError
from achilles.auth.constants import UserRole
from achilles.auth.models import User
from achilles.auth.security.crypto import encrypt_optional, mask_encrypted
from achilles.email.i18n import Locale
from achilles.notifications.constants import (
    EMAIL_DEFAULTS,
    ORG_TYPES,
    PERSONAL_TYPES,
    ChannelKind,
    DeliveryState,
    EventType,
    type_severity,
)
from achilles.notifications.i18n import render
from achilles.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPref,
    NotificationRoute,
)
from achilles.notifications.schemas import (
    ChannelCreate,
    ChannelOut,
    ChannelPatch,
    NotificationOut,
    Pref,
    Prefs,
    RouteCellPatch,
    RouteOut,
)
from achilles.notifications.webhooks import WebhookDeliveryError, post, test_payload

# The feed facet "period" vocabulary → max age.
PERIOD_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def unread_clause() -> sa.ColumnElement[bool]:
    """The one definition of "unread".

    Never read, or a deduped series that re-fired after the last read (its
    last_seen_at outran read_at). Requires Notification to be in the
    statement's FROM.
    """
    return sa.or_(
        NotificationDelivery.read_at.is_(None),
        NotificationDelivery.read_at < Notification.last_seen_at,
    )


def feed_stmt(
    user: User,
    *,
    event_types: list[str] | None = None,
    severities: list[str] | None = None,
    unread_only: bool = False,
    period: str | None = None,
    q: str | None = None,
) -> sa.Select[tuple[Notification, NotificationDelivery]]:
    """One's own feed: the in-app deliveries materialized for this person."""
    stmt = (
        sa.select(Notification, NotificationDelivery)
        .join(
            NotificationDelivery,
            NotificationDelivery.notification_id == Notification.id,
        )
        .join(
            NotificationChannel,
            NotificationChannel.id == NotificationDelivery.channel_id,
        )
        .where(
            NotificationDelivery.user_id == user.id,
            NotificationChannel.kind == ChannelKind.IN_APP.value,
        )
        .order_by(sa.func.coalesce(Notification.last_seen_at, Notification.created_at).desc())
        .order_by(Notification.id.desc())
    )
    if event_types:
        stmt = stmt.where(Notification.event_type.in_(event_types))
    if severities:
        stmt = stmt.where(Notification.severity.in_(severities))
    if unread_only:
        stmt = stmt.where(unread_clause())
    if q and (needle := q.strip()):
        # "by title or source": the title is a catalog key, so the user-visible
        # text lives in title_params — match the source slug, the key and the
        # interpolated values together.
        like = f"%{needle}%"
        stmt = stmt.where(
            sa.or_(
                Notification.source.ilike(like),
                Notification.title.ilike(like),
                sa.cast(Notification.title_params, sa.Text).ilike(like),
            )
        )
    if period in PERIOD_WINDOWS:
        floor = datetime.now(UTC) - PERIOD_WINDOWS[period]
        stmt = stmt.where(
            sa.func.coalesce(Notification.last_seen_at, Notification.created_at) >= floor
        )
    return stmt


def notification_out(
    notification: Notification, delivery: NotificationDelivery, *, locale: Locale
) -> NotificationOut:
    rendered = render(notification.title, notification.title_params, locale)
    return NotificationOut(
        id=notification.id,
        event=notification.title,
        event_type=notification.event_type,
        severity=notification.severity,
        title=rendered.title,
        body=rendered.body,
        source=notification.source,
        source_ref=notification.source_ref,
        dedup_count=notification.dedup_count,
        created_at=notification.created_at,
        last_seen_at=notification.last_seen_at,
        read_at=delivery.read_at,
    )


async def unread_count(session: AsyncSession, user_id: int) -> int:
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(NotificationDelivery)
        .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(
            NotificationDelivery.user_id == user_id,
            NotificationChannel.kind == ChannelKind.IN_APP.value,
            unread_clause(),
        )
    )
    return count or 0


async def mark_read(session: AsyncSession, user: User, notification_id: int) -> None:
    """Idempotent; a foreign (or unknown) notification is an invisible 404."""
    row = (
        await session.execute(
            sa.select(NotificationDelivery, Notification.last_seen_at)
            .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.notification_id == notification_id,
                NotificationDelivery.user_id == user.id,
                NotificationChannel.kind == ChannelKind.IN_APP.value,
            )
        )
    ).first()
    if row is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such notification")
    delivery, last_seen_at = row
    # Unread if never read, or the series re-fired since the last read.
    if delivery.read_at is None or (last_seen_at is not None and delivery.read_at < last_seen_at):
        delivery.read_at = datetime.now(UTC)
        delivery.state = DeliveryState.READ.value
    await session.commit()


async def read_all(session: AsyncSession, user: User) -> None:
    in_app = sa.select(NotificationChannel.id).where(
        NotificationChannel.kind == ChannelKind.IN_APP.value
    )
    await session.execute(
        sa.update(NotificationDelivery)
        .where(
            NotificationDelivery.user_id == user.id,
            NotificationDelivery.channel_id.in_(in_app),
            NotificationDelivery.notification_id == Notification.id,
            unread_clause(),
        )
        .values(read_at=datetime.now(UTC), state=DeliveryState.READ.value)
    )
    await session.commit()


# --- Personal prefs ---


def visible_types(user: User) -> list[EventType]:
    """Org categories are the admins' concern; personal ones are everyone's."""
    if user.role in (UserRole.OWNER.value, UserRole.ADMIN.value):
        return list(EventType)
    return list(PERSONAL_TYPES)


async def effective_prefs(session: AsyncSession, user: User) -> list[Pref]:
    rows = {
        row.event_type: row
        for row in await session.scalars(
            sa.select(NotificationPref).where(NotificationPref.user_id == user.id)
        )
    }
    out: list[Pref] = []
    for event_type in visible_types(user):
        row = rows.get(event_type.value)
        out.append(
            Pref(
                event_type=event_type.value,
                in_app_enabled=row.in_app_enabled if row else True,
                email_enabled=row.email_enabled if row else EMAIL_DEFAULTS[event_type],
            )
        )
    return out


async def put_prefs(session: AsyncSession, user: User, body: Prefs) -> list[Pref]:
    allowed = {t.value for t in visible_types(user)}
    for item in body.items:
        if item.event_type not in allowed:
            raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such notification type")
        await session.execute(
            pg_insert(NotificationPref)
            .values(
                user_id=user.id,
                event_type=item.event_type,
                in_app_enabled=item.in_app_enabled,
                email_enabled=item.email_enabled,
            )
            .on_conflict_do_update(
                constraint="uq_notification_prefs_cell",
                set_={
                    "in_app_enabled": item.in_app_enabled,
                    "email_enabled": item.email_enabled,
                },
            )
        )
    await session.commit()
    return await effective_prefs(session, user)


# --- Admin: channels · routes ---


def channel_out(channel: NotificationChannel, *, key: bytes) -> ChannelOut:
    return ChannelOut(
        id=channel.id,
        kind=channel.kind,
        preset=channel.preset,
        name=channel.name,
        is_builtin=channel.is_builtin,
        enabled=channel.enabled,
        url_mask=mask_encrypted(channel.url_enc, key=key),
        secret_set=bool(channel.secret_enc),
        last_test_ok=channel.last_test_ok,
        last_test_at=channel.last_test_at,
    )


async def list_channels(session: AsyncSession) -> list[NotificationChannel]:
    return list(
        await session.scalars(sa.select(NotificationChannel).order_by(NotificationChannel.id))
    )


async def get_channel(session: AsyncSession, channel_id: int) -> NotificationChannel:
    channel = await session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such channel")
    return channel


async def create_webhook(
    session: AsyncSession, body: ChannelCreate, *, key: bytes
) -> NotificationChannel:
    """A webhook channel + its broadcast route cells (disabled until switched on)."""
    channel = NotificationChannel(
        kind=ChannelKind.WEBHOOK.value,
        preset=body.preset,
        name=body.name,
        url_enc=encrypt_optional(body.url, key=key),
        secret_enc=encrypt_optional(body.secret, key=key),
    )
    session.add(channel)
    await session.flush()
    # Targeted categories never travel over webhooks — no cells for them.
    session.add_all(
        NotificationRoute(event_type=t.value, channel_id=channel.id, enabled=False)
        for t in ORG_TYPES
    )
    await session.commit()
    return channel


async def patch_channel(
    session: AsyncSession, channel: NotificationChannel, body: ChannelPatch, *, key: bytes
) -> NotificationChannel:
    fields = body.model_fields_set
    if channel.is_builtin and fields - {"enabled"}:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "A builtin channel has no editable fields")
    if channel.kind == ChannelKind.IN_APP.value and body.enabled is False:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "The in-app channel cannot be disabled")
    if "name" in fields and body.name:
        channel.name = body.name
    if "url" in fields:
        channel.url_enc = encrypt_optional(body.url, key=key)
    if "secret" in fields:
        channel.secret_enc = encrypt_optional(body.secret, key=key)
    if "enabled" in fields and body.enabled is not None:
        channel.enabled = body.enabled
    await session.commit()
    return channel


async def delete_channel(session: AsyncSession, channel: NotificationChannel) -> None:
    if channel.is_builtin:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "Builtin channels cannot be deleted")
    await session.delete(channel)  # routes CASCADE; delivery rows keep NULL (audit)
    await session.commit()


async def test_channel(
    session: AsyncSession, channel: NotificationChannel, *, key: bytes, locale: Locale
) -> tuple[bool, str | None]:
    """A fabricated event to the endpoint; stamps last_test_*, never a 5xx."""
    if channel.kind != ChannelKind.WEBHOOK.value:
        raise ApiError(409, CODE_CONFLICT, "Conflict", "Only webhook channels are testable")
    ok, error = True, None
    try:
        await post(channel, test_payload(channel.webhook_preset, locale=locale), key=key)
    except WebhookDeliveryError as exc:
        ok, error = False, str(exc)
    channel.last_test_ok = ok
    channel.last_test_at = datetime.now(UTC)
    await session.commit()
    return ok, error


async def list_routes(session: AsyncSession) -> list[RouteOut]:
    rows = await session.scalars(sa.select(NotificationRoute).order_by(NotificationRoute.id))
    return [
        RouteOut(
            event_type=row.event_type,
            severity=str(type_severity(EventType(row.event_type))),
            channel_id=row.channel_id,
            enabled=row.enabled,
            locked=row.locked,
        )
        for row in rows
    ]


async def patch_routes(session: AsyncSession, items: list[RouteCellPatch]) -> list[RouteOut]:
    cells = {
        (row.event_type, row.channel_id): row
        for row in await session.scalars(
            sa.select(NotificationRoute).where(
                sa.tuple_(NotificationRoute.event_type, NotificationRoute.channel_id).in_(
                    [(item.event_type, item.channel_id) for item in items]
                )
            )
        )
    }
    org_type_values = {t.value for t in ORG_TYPES}
    for item in items:
        row = cells.get((item.event_type, item.channel_id))
        if row is None:
            # A gap in the grid (e.g. a channel added before this type existed):
            # materialize the cell on first toggle rather than dead-end on a dash.
            if item.event_type not in org_type_values:
                raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such route cell")
            if await session.get(NotificationChannel, item.channel_id) is None:
                raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such route cell")
            session.add(
                NotificationRoute(
                    event_type=item.event_type, channel_id=item.channel_id, enabled=item.enabled
                )
            )
            continue
        if row.locked and not item.enabled:
            raise ApiError(
                409, CODE_CONFLICT, "Conflict", "This cell is locked open by the platform"
            )
        row.enabled = item.enabled
    await session.commit()
    return await list_routes(session)
