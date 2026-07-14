"""The dispatcher: a module raises an event, the tract does the rest.

Five steps (dispatcher.html): event row (with series dedup) → addressing
(broadcast slice or one person) → fan-out over enabled routes x channels x
personal prefs → deliveries (in-app lands synchronously, email/webhook queue)
→ the caller publishes the queued jobs *after commit* and pings the SSE bus.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import UserRole, UserStatus
from achilles.auth.models import User
from achilles.email.constants import SEND_RETRY_JOB_ARGS
from achilles.infra.redis import PREFIX_PUSH, RedisPools
from achilles.infra.worker.base import Lane, publish, release_claim
from achilles.notifications.constants import (
    EMAIL_DEFAULTS,
    EVENT_CATALOG,
    ChannelKind,
    DeliveryState,
    EventSpec,
)
from achilles.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPref,
    NotificationRoute,
)

logger = logging.getLogger(__name__)

# One SSE bus channel per person; the payload is just a nudge, state lives in PG.
PUSH_CHANNEL = PREFIX_PUSH + "notif:user:{user_id}"

_DELIVERY_JOB = {ChannelKind.EMAIL: "deliver_email", ChannelKind.WEBHOOK: "deliver_webhook"}

# A channel kind is *asynchronous* — it rides a broker delivery job — iff it maps
# to one here. Everything else is *synchronous*: it lands SENT at fan-out and is
# never published or republished (in_app is pinged over SSE instead, and carries
# the extra `read` state). These two sets are the single source of truth for the
# sync/async split; `test_channel_kind_coverage` fails if a new ChannelKind is
# added without being classified, so a future rail can't silently reach — or
# silently miss — the broker. The stuck-delivery sweep filters on ASYNC only, and
# publish_deliveries drops any unmapped kind loudly rather than crashing the batch.
ASYNC_CHANNEL_KINDS = frozenset(_DELIVERY_JOB)
SYNC_CHANNEL_KINDS = frozenset(ChannelKind) - ASYNC_CHANNEL_KINDS

# Who receives broadcasts — the one policy statement for org-wide addressing.
BROADCAST_AUDIENCE = (
    User.role.in_([UserRole.OWNER.value, UserRole.ADMIN.value]),
    User.status == UserStatus.ACTIVE.value,
)


@dataclass(frozen=True, slots=True)
class QueuedDelivery:
    delivery_id: int
    kind: ChannelKind


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What the tract produced; `created=False` means a series increment only."""

    notification_id: int
    created: bool
    recipients: tuple[int, ...] = ()  # in-app recipients — the SSE ping list
    queued: tuple[QueuedDelivery, ...] = field(default_factory=tuple)


def _series_alive(window: timedelta, now: datetime) -> sa.ColumnElement[bool]:
    """The one dedup-window predicate: a series is live while its last touch is inside."""
    return sa.func.coalesce(Notification.last_seen_at, Notification.created_at) > now - window


async def live_dedup_keys(
    session: AsyncSession, keys: list[str], *, event: str, now: datetime
) -> set[str]:
    """Dedup keys already covered by a live notification inside the event's window.

    The threshold-poll pre-check (cron ticks): a covered key is skipped up
    front instead of being re-dispatched into the dedup path, which would
    bump the feed row + dedup_count on every poll.
    """
    if not keys:
        return set()
    window = EVENT_CATALOG[event].dedup_window
    rows = await session.scalars(
        sa.select(Notification.dedup_key).where(
            Notification.dedup_key.in_(keys),
            _series_alive(window, now),
        )
    )
    return {key for key in rows if key is not None}


async def notify(
    session: AsyncSession,
    *,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
    now: datetime | None = None,
) -> DispatchResult:
    """Raise one event; flushes rows, does NOT commit or enqueue.

    The caller commits and then hands the result to :func:`publish_result`
    (or uses :func:`dispatch_and_publish` which does both).
    """
    spec = EVENT_CATALOG[event]
    if spec.targeted and target_user_id is None:
        msg = f"event {event} is targeted — target_user_id is required"
        raise ValueError(msg)
    now = now or datetime.now(UTC)

    if dedup_key is not None:
        # The advisory lock serializes concurrent same-key events (one xact each).
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(dedup_key))))
        existing = await session.scalar(
            sa.select(Notification).where(
                Notification.dedup_key == dedup_key,
                _series_alive(spec.dedup_window, now),
            )
        )
        if existing is not None:
            # A series increment moves the feed only — no new letters/webhooks
            # (that is the whole point of the dedup window).
            existing.dedup_count += 1
            existing.last_seen_at = now
            await session.flush()
            return DispatchResult(notification_id=existing.id, created=False)

    row = Notification(
        event_type=spec.event_type.value,
        severity=spec.severity.value,
        target_user_id=target_user_id,
        title=event,  # the i18n key; rendering happens per reader
        title_params=dict(params or {}),
        source=spec.source,
        source_ref=source_ref,
        dedup_key=dedup_key,
        last_seen_at=now if dedup_key is not None else None,
    )
    session.add(row)
    await session.flush()

    recipients = await _recipients(session, spec, target_user_id)
    queued, in_app_recipients = await _fan_out(session, spec, row, recipients, now)
    return DispatchResult(
        notification_id=row.id,
        created=True,
        recipients=tuple(in_app_recipients),
        queued=tuple(queued),
    )


async def _recipients(
    session: AsyncSession, spec: EventSpec, target_user_id: int | None
) -> list[int]:
    if spec.targeted:
        return [target_user_id] if target_user_id is not None else []
    return list(await session.scalars(sa.select(User.id).where(*BROADCAST_AUDIENCE)))


async def _fan_out(
    session: AsyncSession,
    spec: EventSpec,
    row: Notification,
    recipients: list[int],
    now: datetime,
) -> tuple[list[QueuedDelivery], list[int]]:
    channels = list(
        await session.scalars(
            sa.select(NotificationChannel)
            .join(NotificationRoute, NotificationRoute.channel_id == NotificationChannel.id)
            .where(
                NotificationRoute.event_type == spec.event_type.value,
                NotificationRoute.enabled,
                NotificationChannel.enabled,
            )
        )
    )

    prefs = {
        pref.user_id: pref
        for pref in await session.scalars(
            sa.select(NotificationPref).where(
                NotificationPref.user_id.in_(recipients),
                NotificationPref.event_type == spec.event_type.value,
            )
        )
    }

    values: list[dict[str, object]] = []
    for channel in channels:
        kind = ChannelKind(channel.kind)
        if kind is ChannelKind.WEBHOOK:
            if spec.targeted:
                continue  # personal events never leave over webhooks
            values.append(
                {
                    "notification_id": row.id,
                    "channel_id": channel.id,
                    "user_id": None,
                    "state": DeliveryState.QUEUED.value,
                    # multirow VALUES needs uniform keys across all dicts
                    "sent_at": None,
                }
            )
            continue
        for user_id in recipients:
            pref = prefs.get(user_id)
            wanted = (
                (pref.in_app_enabled if pref else True)
                if kind is ChannelKind.IN_APP
                else (pref.email_enabled if pref else EMAIL_DEFAULTS[spec.event_type])
            )
            if not wanted:
                continue
            # QUEUED only for a kind that actually has a broker rail; every
            # synchronous kind lands SENT here and never reaches publish. Keying
            # off ASYNC_CHANNEL_KINDS (not `kind is IN_APP`) keeps a future
            # synchronous kind from being mis-queued into a job that isn't there.
            is_async = kind in ASYNC_CHANNEL_KINDS
            values.append(
                {
                    "notification_id": row.id,
                    "channel_id": channel.id,
                    "user_id": user_id,
                    "state": (DeliveryState.QUEUED if is_async else DeliveryState.SENT).value,
                    "sent_at": None if is_async else now,
                }
            )

    if not values:
        return [], []

    # Idempotent against a repeated fan-out (retried caller): the UNIQUE cells
    # swallow duplicates, RETURNING reports only the rows actually written.
    inserted = (
        await session.execute(
            pg_insert(NotificationDelivery)
            .values(values)
            .on_conflict_do_nothing()
            .returning(
                NotificationDelivery.id,
                NotificationDelivery.channel_id,
                NotificationDelivery.user_id,
                NotificationDelivery.state,
            )
        )
    ).all()

    kind_by_channel = {channel.id: ChannelKind(channel.kind) for channel in channels}
    queued = [
        QueuedDelivery(delivery_id=delivery_id, kind=kind_by_channel[channel_id])
        for delivery_id, channel_id, _user_id, state in inserted
        if state == DeliveryState.QUEUED.value
    ]
    in_app_recipients = [
        user_id
        for _delivery_id, _channel_id, user_id, state in inserted
        if state == DeliveryState.SENT.value and user_id is not None
    ]
    return queued, in_app_recipients


async def publish_deliveries(
    redis: RedisPools, *, queue_url: str, items: tuple[QueuedDelivery, ...]
) -> None:
    """Queue external deliveries; `ndel:{id}` claims make a repeat publish a no-op."""
    sends = []
    for item in items:
        job = _DELIVERY_JOB.get(item.kind)
        if job is None:
            # A synchronous kind has no broker job; it must never arrive here.
            # One stray row (a sync delivery wrongly left QUEUED) must not sink
            # the whole batch/cron — log and skip it, don't KeyError.
            logger.error(
                "no delivery job for kind %s (delivery %s) — skipped",
                item.kind,
                item.delivery_id,
            )
            continue
        sends.append(
            publish(
                queue_url,
                redis.durable,
                Lane.INTERACTIVE,
                job,
                job_id=f"ndel:{item.delivery_id}",
                delivery_id=item.delivery_id,
                **SEND_RETRY_JOB_ARGS,
            )
        )
    await asyncio.gather(*sends)


async def republish_deliveries(
    redis: RedisPools, *, queue_url: str, items: tuple[QueuedDelivery, ...]
) -> None:
    """The sweep face: drop the enqueue claims, then publish anew.

    A claim outlives a crashed enqueue or a job that died mid-flight — without
    the release every re-publish is a no-op for the claim's full TTL. SAQ's own
    incomplete-set still dedups a job that is merely slow or between retries,
    so releasing the claim never doubles a letter.
    """
    for item in items:
        await release_claim(redis.durable, job_id=f"ndel:{item.delivery_id}")
    await publish_deliveries(redis, queue_url=queue_url, items=items)


async def publish_result(redis: RedisPools, *, queue_url: str, result: DispatchResult) -> None:
    """After commit: queue the external deliveries, nudge the SSE bus."""
    await publish_deliveries(redis, queue_url=queue_url, items=result.queued)
    for user_id in result.recipients:
        try:
            await redis.cache.publish(PUSH_CHANNEL.format(user_id=user_id), "1")
        except Exception:  # the bell nudge is best-effort
            logger.warning("SSE nudge for user %s failed", user_id, exc_info=True)


async def dispatch_and_publish(
    session: AsyncSession,
    redis: RedisPools,
    *,
    queue_url: str,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
) -> DispatchResult:
    """The typical call site: notify → commit → publish."""
    result = await notify(
        session,
        event=event,
        target_user_id=target_user_id,
        source_ref=source_ref,
        params=params,
        dedup_key=dedup_key,
    )
    await session.commit()
    await publish_result(redis, queue_url=queue_url, result=result)
    return result
