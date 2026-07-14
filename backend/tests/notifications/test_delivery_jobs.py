"""Delivery jobs: state guards, language chain, retries, the cron sweep (integration)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.models import ModelUsage
from achilles.auth.models import ApiKey
from achilles.config import Settings
from achilles.email.constants import TransientSendError
from achilles.notifications import dispatcher
from achilles.notifications import jobs as notification_jobs
from achilles.notifications.constants import ChannelKind, DeliveryState
from achilles.notifications.models import Notification, NotificationChannel, NotificationDelivery
from tests.auth.integration.conftest import Outbox, set_smtp
from tests.factories.users import create_user
from tests.notifications.conftest import WEBHOOK_URL, create_webhook_channel

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", None)


@pytest.fixture(autouse=True)
def job_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(notification_jobs, "app_settings", test_settings)
    monkeypatch.setattr(dispatcher, "publish", _noop_publish)


async def _noop_publish(*args: object, **kwargs: object) -> bool:
    return True


async def _one_queued_email(session: AsyncSession) -> int:
    """Broadcast an event and return its queued email delivery id."""
    result = await dispatcher.notify(session, event="system.backup_failed")
    await session.commit()
    assert len(result.queued) == 1
    return result.queued[0].delivery_id


async def test_deliver_email_renders_the_recipient_language(
    db_session: AsyncSession, outbox: Outbox
):
    owner = await create_user(db_session, role="owner")
    owner.locale = "en"
    await db_session.commit()
    delivery_id = await _one_queued_email(db_session)

    await notification_jobs.deliver_email(CTX, delivery_id=delivery_id)

    (letter,) = outbox.letters
    assert letter.to == owner.email
    assert letter.subject == "Backup failed"
    assert "/inbox" in letter.text

    row = await db_session.get(NotificationDelivery, delivery_id)
    assert row is not None
    await db_session.refresh(row)
    assert row.state == DeliveryState.SENT.value and row.sent_at is not None


async def test_deliver_email_is_a_noop_once_sent(db_session: AsyncSession, outbox: Outbox):
    await create_user(db_session, role="owner")
    delivery_id = await _one_queued_email(db_session)

    await notification_jobs.deliver_email(CTX, delivery_id=delivery_id)
    await notification_jobs.deliver_email(CTX, delivery_id=delivery_id)  # the retry
    assert len(outbox.letters) == 1, "the state guard swallows the second run"


async def test_deliver_email_without_smtp_marks_failed(db_session: AsyncSession, outbox: Outbox):
    await create_user(db_session, role="owner")
    delivery_id = await _one_queued_email(db_session)
    await set_smtp(db_session, enabled=False)

    await notification_jobs.deliver_email(CTX, delivery_id=delivery_id)
    assert outbox.letters == []
    row = await db_session.get(NotificationDelivery, delivery_id)
    assert row is not None
    await db_session.refresh(row)
    assert (row.state, row.error) == (DeliveryState.FAILED.value, "smtp_not_configured")


async def test_deliver_email_transient_failure_raises_for_retry(
    db_session: AsyncSession, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
):
    del outbox
    await create_user(db_session, role="owner")
    delivery_id = await _one_queued_email(db_session)

    async def flaky(*args: object, **kwargs: object) -> None:
        raise TransientSendError("450 later")

    monkeypatch.setattr("achilles.email.smtp.send", flaky)
    with pytest.raises(TransientSendError):
        await notification_jobs.deliver_email(CTX, delivery_id=delivery_id)

    row = await db_session.get(NotificationDelivery, delivery_id)
    assert row is not None
    await db_session.refresh(row)
    assert row.state == DeliveryState.QUEUED.value, "still queued — the retry re-runs it"


async def test_deliver_webhook_posts_and_stamps(
    db_session: AsyncSession,
    test_settings: Settings,
    hibp_clean: respx.MockRouter,
    outbox: Outbox,
):
    del outbox
    await create_user(db_session, role="owner")
    await create_webhook_channel(db_session, test_settings)
    route = hibp_clean.post(WEBHOOK_URL).mock(return_value=Response(200))

    result = await dispatcher.notify(db_session, event="sync.source_unreachable")
    await db_session.commit()
    webhook_items = [i for i in result.queued if i.kind.value == "webhook"]
    assert len(webhook_items) == 1

    await notification_jobs.deliver_webhook(CTX, delivery_id=webhook_items[0].delivery_id)
    assert route.called
    row = await db_session.get(NotificationDelivery, webhook_items[0].delivery_id)
    assert row is not None
    await db_session.refresh(row)
    assert row.state == DeliveryState.SENT.value


async def test_deliver_webhook_refusal_marks_failed(
    db_session: AsyncSession,
    test_settings: Settings,
    hibp_clean: respx.MockRouter,
    outbox: Outbox,
):
    del outbox
    await create_user(db_session, role="owner")
    await create_webhook_channel(db_session, test_settings)
    hibp_clean.post(WEBHOOK_URL).mock(return_value=Response(404))

    result = await dispatcher.notify(db_session, event="sync.source_unreachable")
    await db_session.commit()
    (item,) = [i for i in result.queued if i.kind.value == "webhook"]

    await notification_jobs.deliver_webhook(CTX, delivery_id=item.delivery_id)
    row = await db_session.get(NotificationDelivery, item.delivery_id)
    assert row is not None
    await db_session.refresh(row)
    assert row.state == DeliveryState.FAILED.value
    assert "http_404" in (row.error or "")


async def test_raise_event_dispatches_from_a_sessionless_context(
    db_session: AsyncSession, outbox: Outbox
):
    del outbox
    owner = await create_user(db_session, role="owner")
    await notification_jobs.raise_event(
        CTX,
        event="security.brute_force",
        params={"email": "x@y.z"},
        dedup_key="brute:abc",
    )
    count = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(NotificationDelivery)
        .where(NotificationDelivery.user_id == owner.id)
    )
    assert count and count >= 1


async def test_tick_raises_api_key_and_budget_events(
    db_session: AsyncSession, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
):
    del outbox
    await create_user(db_session, role="owner")
    key_owner = await create_user(db_session)
    now = datetime.now(UTC)
    db_session.add(
        ApiKey(
            user_id=key_owner.id,
            key_hash="h1",
            prefix="ak_test",
            scope={},
            expires_at=now + timedelta(days=3),
        )
    )
    db_session.add(
        ModelUsage(
            model_id=None,
            function="chat",
            bucket_date=now.date(),
            request_count=1,
            input_tokens=10,
            output_tokens=10,
            cost=Decimal("100.00"),
        )
    )
    await db_session.execute(
        sa.text(
            "UPDATE platform_settings SET ai_monthly_budget = 50, ai_budget_alert_enabled = true"
        )
    )
    await db_session.commit()

    await notification_jobs.notifications_tick(CTX)

    titles = set(await db_session.scalars(sa.select(Notification.title)))
    assert "security.api_key_expiring" in titles
    assert "budget.ai_monthly_exceeded" in titles

    # the second tick dedups both threshold events — the feed stays quiet
    await notification_jobs.notifications_tick(CTX)
    total = await db_session.scalar(sa.select(sa.func.count()).select_from(Notification))
    assert total == 2


async def test_sweep_republished_stuck_deliveries(
    db_session: AsyncSession, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
):
    del outbox
    await create_user(db_session, role="owner")
    delivery_id = await _one_queued_email(db_session)
    # age the row past the stuck threshold
    await db_session.execute(
        sa.text("UPDATE notification_deliveries SET created_at = created_at - interval '1 hour'")
    )
    await db_session.commit()

    published: list[int] = []

    async def capture_publish(redis: object, *, queue_url: str, items: object) -> None:
        published.extend(item.delivery_id for item in items)  # type: ignore[attr-defined]

    monkeypatch.setattr(notification_jobs.dispatcher, "publish_deliveries", capture_publish)
    await notification_jobs.notifications_tick(CTX)
    assert published == [delivery_id]


def test_channel_kind_coverage():
    """Every kind is classified sync xor async — the split's single source of truth.

    A new ChannelKind added without a broker job (or wrongly given one) would slip
    past the stuck-sweep filter and crash publish; this pins the invariant so that
    omission fails here instead of in the cron.
    """
    assert set(ChannelKind) == dispatcher.SYNC_CHANNEL_KINDS | dispatcher.ASYNC_CHANNEL_KINDS, (
        "some ChannelKind is neither synchronous nor broker-backed"
    )
    assert not (dispatcher.SYNC_CHANNEL_KINDS & dispatcher.ASYNC_CHANNEL_KINDS)
    assert ChannelKind.IN_APP in dispatcher.SYNC_CHANNEL_KINDS


async def test_sweep_skips_synchronous_stuck_delivery(
    db_session: AsyncSession, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
):
    """A synchronous in_app delivery wrongly left QUEUED is a data anomaly, not a
    missed publish: the sweep must ignore it (it has no job) instead of crashing."""
    del outbox
    await create_user(db_session, role="owner")
    email_id = await _one_queued_email(db_session)  # also seeds the in_app SENT rows
    in_app_id = await db_session.scalar(
        sa.select(NotificationDelivery.id)
        .join(NotificationChannel, NotificationChannel.id == NotificationDelivery.channel_id)
        .where(NotificationChannel.kind == ChannelKind.IN_APP.value)
        .limit(1)
    )
    assert in_app_id is not None
    await db_session.execute(
        sa.update(NotificationDelivery)
        .where(NotificationDelivery.id == in_app_id)
        .values(state=DeliveryState.QUEUED.value)
    )
    # age every row past the stuck threshold
    await db_session.execute(
        sa.text("UPDATE notification_deliveries SET created_at = created_at - interval '1 hour'")
    )
    await db_session.commit()

    published: list[int] = []

    async def capture_publish(redis: object, *, queue_url: str, items: object) -> None:
        published.extend(item.delivery_id for item in items)  # type: ignore[attr-defined]

    monkeypatch.setattr(notification_jobs.dispatcher, "publish_deliveries", capture_publish)
    await notification_jobs.notifications_tick(CTX)
    assert published == [email_id], "only the broker-backed delivery is swept; in_app is skipped"


async def test_publish_deliveries_skips_unmapped_kind(caplog: pytest.LogCaptureFixture):
    """One stray synchronous item must be logged and skipped, not KeyError the batch."""

    class _Redis:
        durable = None

    items = (
        dispatcher.QueuedDelivery(delivery_id=1, kind=ChannelKind.IN_APP),  # no job → skip
        dispatcher.QueuedDelivery(
            delivery_id=2, kind=ChannelKind.EMAIL
        ),  # has job → publish (noop)
    )
    with caplog.at_level("ERROR"):
        await dispatcher.publish_deliveries(
            cast("dispatcher.RedisPools", _Redis()), queue_url="q", items=items
        )
    assert "no delivery job for kind" in caplog.text
