"""Dispatcher tract: addressing, fan-out, prefs, dedup — tests.html (integration)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.config import Settings
from achilles.notifications import dispatcher
from achilles.notifications.constants import ChannelKind, DeliveryState
from achilles.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationPref,
    NotificationRoute,
)
from tests.factories.users import create_user
from tests.notifications.conftest import create_webhook_channel

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def _deliveries(session: AsyncSession, notification_id: int) -> list[NotificationDelivery]:
    return list(
        await session.scalars(
            sa.select(NotificationDelivery)
            .where(NotificationDelivery.notification_id == notification_id)
            .order_by(NotificationDelivery.id)
        )
    )


async def _channel_id(session: AsyncSession, kind: str) -> int:
    channel_id = await session.scalar(
        sa.select(NotificationChannel.id).where(
            NotificationChannel.kind == kind, NotificationChannel.is_builtin
        )
    )
    assert channel_id is not None
    return channel_id


async def test_broadcast_reaches_the_admin_slice_only(db_session: AsyncSession):
    owner = await create_user(db_session, role="owner")
    admin = await create_user(db_session, role="admin")
    await create_user(db_session, role="member")
    await create_user(db_session, role="admin", status="deactivated")

    result = await dispatcher.notify(
        db_session,
        event="system.backup_failed",
        source_ref="backup/1",
    )
    await db_session.commit()

    assert result.created is True
    rows = await _deliveries(db_session, result.notification_id)
    in_app = await _channel_id(db_session, "in_app")
    email = await _channel_id(db_session, "email")
    by_cell = {(row.channel_id, row.user_id): row for row in rows}

    # in-app lands synchronously for both admins; the member and the
    # deactivated admin get nothing
    assert by_cell[(in_app, owner.id)].state == DeliveryState.SENT.value
    assert by_cell[(in_app, admin.id)].state == DeliveryState.SENT.value
    # platform types default email ON (opt-out) → queued letters
    assert by_cell[(email, owner.id)].state == DeliveryState.QUEUED.value
    assert by_cell[(email, admin.id)].state == DeliveryState.QUEUED.value
    assert len(rows) == 4
    assert sorted(result.recipients) == sorted([owner.id, admin.id])
    assert {item.kind for item in result.queued} == {ChannelKind.EMAIL}


async def test_targeted_event_defaults_email_opt_in(db_session: AsyncSession):
    await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")

    result = await dispatcher.notify(
        db_session,
        event="agent.admin_paused",
        target_user_id=member.id,
        params={"agent_name": "Watcher"},
        source_ref="agent/7",
    )
    await db_session.commit()

    rows = await _deliveries(db_session, result.notification_id)
    # personal types: in-app yes, email opt-in (no prefs row → off), owner uninvolved
    assert [(row.user_id, row.state) for row in rows] == [(member.id, DeliveryState.SENT.value)]
    assert result.queued == ()


async def test_targeted_event_requires_a_target(db_session: AsyncSession):
    with pytest.raises(ValueError, match="targeted"):
        await dispatcher.notify(db_session, event="agent.run_failed")


async def test_prefs_narrow_the_fanout(db_session: AsyncSession):
    owner = await create_user(db_session, role="owner")
    db_session.add(
        NotificationPref(
            user_id=owner.id, event_type="system", in_app_enabled=False, email_enabled=True
        )
    )
    await db_session.commit()

    result = await dispatcher.notify(db_session, event="system.backup_failed")
    await db_session.commit()

    rows = await _deliveries(db_session, result.notification_id)
    email = await _channel_id(db_session, "email")
    assert [(row.channel_id, row.state) for row in rows] == [(email, DeliveryState.QUEUED.value)], (
        "in-app muted personally, the email letter still goes"
    )


async def test_email_opt_in_pref_enables_personal_letters(db_session: AsyncSession):
    member = await create_user(db_session, role="member")
    db_session.add(
        NotificationPref(
            user_id=member.id, event_type="agent", in_app_enabled=True, email_enabled=True
        )
    )
    await db_session.commit()

    result = await dispatcher.notify(db_session, event="agent.run_failed", target_user_id=member.id)
    await db_session.commit()
    assert {item.kind for item in result.queued} == {ChannelKind.EMAIL}


async def test_disabled_route_and_disabled_channel_are_skipped(db_session: AsyncSession):
    await create_user(db_session, role="owner")
    email = await _channel_id(db_session, "email")
    await db_session.execute(
        sa.update(NotificationRoute)
        .where(NotificationRoute.event_type == "system", NotificationRoute.channel_id == email)
        .values(enabled=False)
    )
    await db_session.commit()

    result = await dispatcher.notify(db_session, event="system.backup_failed")
    await db_session.commit()
    rows = await _deliveries(db_session, result.notification_id)
    assert [row.channel_id for row in rows] == [await _channel_id(db_session, "in_app")]

    # now the whole email channel off — same effect for another type
    await db_session.execute(
        sa.update(NotificationChannel).where(NotificationChannel.id == email).values(enabled=False)
    )
    await db_session.commit()
    result2 = await dispatcher.notify(db_session, event="budget.ai_monthly_exceeded")
    await db_session.commit()
    rows2 = await _deliveries(db_session, result2.notification_id)
    assert [row.channel_id for row in rows2] == [await _channel_id(db_session, "in_app")]


async def test_webhook_gets_broadcast_only(db_session: AsyncSession, test_settings: Settings):
    await create_user(db_session, role="owner")
    member = await create_user(db_session, role="member")
    channel = await create_webhook_channel(db_session, test_settings)

    broadcast = await dispatcher.notify(db_session, event="sync.source_unreachable")
    await db_session.commit()
    rows = await _deliveries(db_session, broadcast.notification_id)
    webhook_rows = [row for row in rows if row.channel_id == channel.id]
    assert len(webhook_rows) == 1
    assert webhook_rows[0].user_id is None
    assert ChannelKind.WEBHOOK in {item.kind for item in broadcast.queued}

    targeted = await dispatcher.notify(
        db_session, event="agent.run_failed", target_user_id=member.id
    )
    await db_session.commit()
    targeted_rows = await _deliveries(db_session, targeted.notification_id)
    assert all(row.channel_id != channel.id for row in targeted_rows)


async def test_dedup_increments_the_series_without_new_deliveries(db_session: AsyncSession):
    await create_user(db_session, role="owner")

    first = await dispatcher.notify(
        db_session, event="sync.source_unreachable", dedup_key="probe:5"
    )
    await db_session.commit()
    second = await dispatcher.notify(
        db_session, event="sync.source_unreachable", dedup_key="probe:5"
    )
    await db_session.commit()

    assert second.created is False
    assert second.notification_id == first.notification_id
    assert second.queued == () and second.recipients == ()

    row = await db_session.get(Notification, first.notification_id)
    assert row is not None and row.dedup_count == 2

    total = await db_session.scalar(sa.select(sa.func.count()).select_from(Notification))
    assert total == 1, "the increment moved the feed row, not a second event"


async def test_dedup_window_expiry_starts_a_new_series(db_session: AsyncSession):
    await create_user(db_session, role="owner")
    started = datetime.now(UTC) - timedelta(hours=2)

    first = await dispatcher.notify(
        db_session, event="sync.source_unreachable", dedup_key="probe:9", now=started
    )
    await db_session.commit()
    second = await dispatcher.notify(
        db_session, event="sync.source_unreachable", dedup_key="probe:9"
    )
    await db_session.commit()

    assert second.created is True
    assert second.notification_id != first.notification_id
