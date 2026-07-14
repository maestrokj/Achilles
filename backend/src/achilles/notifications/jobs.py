"""SAQ jobs: external delivery (email · webhook), sessionless raise, the cron tick.

Delivery jobs are idempotent by a state guard (a `sent` row is a no-op) and by
the enqueue claim on `ndel:{id}` — a retry is the same job, never a second
letter. The tick owns the cron-born events (API-key expiry, the AI budget)
and re-publishes deliveries stuck in `queued` past the commit→enqueue gap.
"""

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.service import org_zone
from achilles.ai_foundation.services import usage_read
from achilles.auth.models import ApiKey, User
from achilles.config import settings as app_settings
from achilles.db.connections import close_connections, create_connections
from achilles.email import service as email_service
from achilles.email import smtp
from achilles.email.compose import compose
from achilles.email.constants import (
    SMTP_SEND_TIMEOUT_SECONDS,
    EmailKind,
    PermanentSendError,
    TransientSendError,
)
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services import platform
from achilles.notifications import dispatcher, webhooks
from achilles.notifications.api import dispatch_from_worker
from achilles.notifications.constants import (
    API_KEY_EXPIRY_HORIZON,
    STUCK_DELIVERY_AGE,
    ChannelKind,
    DeliveryState,
)
from achilles.notifications.i18n import render
from achilles.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
)

logger = logging.getLogger(__name__)


async def _load_delivery(
    session: AsyncSession, delivery_id: int
) -> tuple[NotificationDelivery, Notification] | None:
    delivery = await session.get(NotificationDelivery, delivery_id)
    if delivery is None or delivery.state != DeliveryState.QUEUED.value:
        return None  # already handled (or gone) — the retry is a no-op
    notification = await session.get(Notification, delivery.notification_id)
    if notification is None:  # pragma: no cover — CASCADE removes deliveries first
        return None
    return delivery, notification


async def _fail(session: AsyncSession, delivery: NotificationDelivery, error: str) -> None:
    """The terminal FAILED stamp — one commit, one protocol for every dead end."""
    delivery.state = DeliveryState.FAILED.value
    delivery.error = error
    await session.commit()


async def _mark_sent(session: AsyncSession, delivery: NotificationDelivery) -> None:
    delivery.state = DeliveryState.SENT.value
    delivery.sent_at = datetime.now(UTC)
    await session.commit()


async def deliver_email(ctx: Context, *, delivery_id: int) -> None:
    """One queued email delivery: render in the recipient language, send, stamp."""
    del ctx
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            loaded = await _load_delivery(session, delivery_id)
            if loaded is None:
                return
            delivery, notification = loaded
            user = await session.get(User, delivery.user_id) if delivery.user_id else None
            if user is None:
                await _fail(session, delivery, "no_recipient")
                return

            smtp_row = await email_service.get_settings(session)
            if not smtp_row.is_available:
                await _fail(session, delivery, "smtp_not_configured")
                return

            locale, branding = await email_service.letter_context(session, user)
            rendered = render(notification.title, notification.title_params, locale)
            composed = compose(
                EmailKind.NOTIFICATION,
                locale=locale,
                branding=branding,
                params={"title": rendered.title, "body": rendered.body or ""},
                action_url=app_settings.public_url("/inbox"),
            )
            try:
                await smtp.send(
                    smtp_row,
                    key=crypto_key,
                    to=user.email,
                    composed=composed,
                    send_timeout=SMTP_SEND_TIMEOUT_SECONDS,
                )
            except PermanentSendError as exc:
                await _fail(session, delivery, str(exc))
                return
            except TransientSendError:
                await session.rollback()
                raise  # SAQ retries the same job — never a second letter
            await _mark_sent(session, delivery)
    finally:
        await close_connections(db)


async def deliver_webhook(ctx: Context, *, delivery_id: int) -> None:
    """One queued webhook POST, in the org language (broadcast only by design)."""
    del ctx
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    try:
        async with db.pg_session_factory() as session:
            loaded = await _load_delivery(session, delivery_id)
            if loaded is None:
                return
            delivery, notification = loaded
            channel = (
                await session.get(NotificationChannel, delivery.channel_id)
                if delivery.channel_id
                else None
            )
            if channel is None or not channel.enabled or channel.kind != ChannelKind.WEBHOOK:
                await _fail(session, delivery, "channel_gone")
                return

            locale = await email_service.org_locale(session)
            payload = webhooks.build_payload(channel.webhook_preset, notification, locale=locale)
            try:
                await webhooks.post(channel, payload, key=crypto_key)
            except webhooks.WebhookDeliveryError as exc:
                await _fail(session, delivery, str(exc))
                return
            await _mark_sent(session, delivery)
    finally:
        await close_connections(db)


async def raise_event(
    ctx: Context,
    *,
    event: str,
    target_user_id: int | None = None,
    source_ref: str | None = None,
    params: dict[str, object] | None = None,
    dedup_key: str | None = None,
) -> None:
    """The sessionless entry: contexts with no DB at hand enqueue this instead."""
    del ctx
    db = create_connections(app_settings)
    try:
        # The worker facade owns the pools and the swallow-and-warn policy.
        await dispatch_from_worker(
            db.pg_session_factory,
            event=event,
            target_user_id=target_user_id,
            source_ref=source_ref,
            params=params,
            dedup_key=dedup_key,
        )
    finally:
        await close_connections(db)


async def notifications_tick(ctx: Context) -> None:
    """Cron: threshold events (API-key expiry, AI budget) + the stuck-queued sweep."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        async with db.pg_session_factory() as session:
            org = await platform.get_platform_settings(session)
            if org.maintenance_mode:
                return
            now = datetime.now(UTC)
            await _tick_api_keys(session, redis, now=now)
            await _tick_budget(session, redis, org=org, now=now)
            await _sweep_stuck(session, redis, now=now)
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def _tick_api_keys(session: AsyncSession, redis: RedisPools, *, now: datetime) -> None:
    rows = (
        await session.execute(
            sa.select(ApiKey, User.full_name)
            .join(User, User.id == ApiKey.user_id)
            .where(
                ApiKey.is_revoked.is_(False),
                ApiKey.expires_at.is_not(None),
                ApiKey.expires_at > now,
                ApiKey.expires_at <= now + API_KEY_EXPIRY_HORIZON,
            )
        )
    ).all()
    # A threshold poll is not a new occurrence: keys already covered by a live
    # notification are skipped up front, not re-dispatched into the dedup path
    # (which would bump the feed row + dedup_count every five minutes).
    live = await dispatcher.live_dedup_keys(
        session, [f"apikey:{k.id}" for k, _ in rows], event="security.api_key_expiring", now=now
    )
    for api_key, user_name in rows:
        if f"apikey:{api_key.id}" in live:
            continue
        await dispatcher.dispatch_and_publish(
            session,
            redis,
            queue_url=app_settings.redis_durable_url,
            event="security.api_key_expiring",
            source_ref=f"api-key/{api_key.id}",
            params={
                "key_prefix": api_key.prefix,
                "user_name": user_name,
                "expires_on": api_key.expires_at.date().isoformat() if api_key.expires_at else "",
            },
            dedup_key=f"apikey:{api_key.id}",
        )


async def _tick_budget(
    session: AsyncSession, redis: RedisPools, *, org: PlatformSettings, now: datetime
) -> None:
    budget = org.ai_monthly_budget
    if not org.ai_budget_alert_enabled or budget is None:
        return
    # Cost accounting owns the month anchor (org timezone, not UTC) and the
    # spend sum — the tick only holds the threshold.
    month = usage_read.month_start(now, org_zone(org))
    month_key = month.strftime("%Y-%m")
    dedup_key = f"budget:{month_key}"
    if await dispatcher.live_dedup_keys(
        session, [dedup_key], event="budget.ai_monthly_exceeded", now=now
    ):
        return  # this month's alert is already up — a poll is not a new event
    spent = await usage_read.monthly_spend(session, since_local_date=month)
    if spent <= budget:
        return
    await dispatcher.dispatch_and_publish(
        session,
        redis,
        queue_url=app_settings.redis_durable_url,
        event="budget.ai_monthly_exceeded",
        source_ref="ai-usage",
        params={"month": month_key, "budget": str(budget)},
        dedup_key=dedup_key,
    )


async def _sweep_stuck(session: AsyncSession, redis: RedisPools, *, now: datetime) -> None:
    """Re-publish deliveries stuck in `queued` (the commit→enqueue gap insurance).

    Same job_id as the original publish, but through the republish face: it
    drops the enqueue claim first (a claim outlives a crashed enqueue or a
    dead job and would otherwise no-op the sweep for the claim's full TTL),
    while SAQ's incomplete-set still dedups a job that is merely slow.
    """
    rows = (
        await session.execute(
            sa.select(NotificationDelivery.id, NotificationChannel.kind)
            .join(
                NotificationChannel,
                NotificationChannel.id == NotificationDelivery.channel_id,
            )
            .where(
                NotificationDelivery.state == DeliveryState.QUEUED.value,
                NotificationDelivery.created_at < now - STUCK_DELIVERY_AGE,
                # Only broker-backed kinds can be "stuck awaiting the broker". A
                # synchronous kind (in_app) sitting in QUEUED is a data anomaly,
                # not a missed publish — republishing it has no job and would
                # crash the sweep, so it is deliberately out of scope here.
                NotificationChannel.kind.in_([k.value for k in dispatcher.ASYNC_CHANNEL_KINDS]),
            )
        )
    ).all()
    for delivery_id, kind in rows:
        await dispatcher.republish_deliveries(
            redis,
            queue_url=app_settings.redis_durable_url,
            items=(dispatcher.QueuedDelivery(delivery_id=delivery_id, kind=ChannelKind(kind)),),
        )
        logger.info("re-published stuck delivery %s (%s)", delivery_id, kind)
