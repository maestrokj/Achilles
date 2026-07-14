"""Notifications tables — mirrors notifications/_workzone/data-model.html.

Channels (where), routes (org matrix: type x channel), prefs (personal
narrowing), notifications (event journal, doubles as the in-app feed and
audit) and deliveries (per-channel outcome + in-app read state).
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin, enum_check
from achilles.notifications.constants import (
    ChannelKind,
    DeliveryState,
    EventType,
    Severity,
    WebhookPreset,
)


class NotificationChannel(TimestampMixin, Base):
    """A place notifications can go: builtin in_app/email or an admin webhook."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        sa.CheckConstraint(
            f"kind IN ({enum_check(ChannelKind)})", name="ck_notification_channels_kind"
        ),
        sa.CheckConstraint(
            f"preset IS NULL OR preset IN ({enum_check(WebhookPreset)})",
            name="ck_notification_channels_preset",
        ),
        sa.CheckConstraint(
            "kind <> 'in_app' OR enabled", name="ck_notification_channels_in_app_on"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    preset: Mapped[str | None] = mapped_column(sa.Text)  # webhook payload dialect
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    url_enc: Mapped[str | None] = mapped_column(sa.Text)  # AES-256-GCM, write-only
    secret_enc: Mapped[str | None] = mapped_column(sa.Text)  # HMAC secret (generic)
    is_builtin: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    last_test_ok: Mapped[bool | None] = mapped_column(sa.Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    @property
    def webhook_preset(self) -> WebhookPreset:
        """The payload dialect; a preset-less webhook speaks generic."""
        return WebhookPreset(self.preset) if self.preset else WebhookPreset.GENERIC


class NotificationRoute(TimestampMixin, Base):
    """One matrix cell: does `event_type` go to `channel`? Locked = locked open."""

    __tablename__ = "notification_routes"
    __table_args__ = (
        sa.CheckConstraint(
            f"event_type IN ({enum_check(EventType)})", name="ck_notification_routes_type"
        ),
        sa.CheckConstraint("enabled OR NOT locked", name="ck_notification_routes_locked_open"),
        sa.UniqueConstraint("event_type", "channel_id", name="uq_notification_routes_cell"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    channel_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    locked: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )


class NotificationPref(TimestampMixin, Base):
    """Personal narrowing of the org matrix; no row = the catalog default."""

    __tablename__ = "notification_prefs"
    __table_args__ = (
        sa.CheckConstraint(
            f"event_type IN ({enum_check(EventType)})", name="ck_notification_prefs_type"
        ),
        sa.UniqueConstraint("user_id", "event_type", name="uq_notification_prefs_cell"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    # No server default on purpose — the INSERT writes the catalog default.
    email_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)


class Notification(TimestampMixin, Base):
    """One event: the in-app feed row and the audit fact; a series dedups here."""

    __tablename__ = "notifications"
    __table_args__ = (
        sa.CheckConstraint(
            f"event_type IN ({enum_check(EventType)})", name="ck_notifications_type"
        ),
        sa.CheckConstraint(
            f"severity IN ({enum_check(Severity)})", name="ck_notifications_severity"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    severity: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # NULL = broadcast to the Owner/Admin slice
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)  # i18n key, not a string
    body: Mapped[str | None] = mapped_column(sa.Text)
    title_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    source: Mapped[str | None] = mapped_column(sa.Text)  # originating module slug
    source_ref: Mapped[str | None] = mapped_column(sa.Text)  # deep link (agent/7, run/42)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    dedup_key: Mapped[str | None] = mapped_column(sa.Text, index=True)  # NULL = never dedups
    dedup_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class NotificationDelivery(TimestampMixin, Base):
    """One notification x channel (x person): the outcome and the read state."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        sa.CheckConstraint(
            f"state IN ({enum_check(DeliveryState)})",
            name="ck_notification_deliveries_state",
        ),
        sa.UniqueConstraint(
            "notification_id", "channel_id", "user_id", name="uq_notification_deliveries_cell"
        ),
        sa.Index(
            "uq_notification_deliveries_channel_only",
            "notification_id",
            "channel_id",
            unique=True,
            postgresql_where=sa.text("user_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("notification_channels.id", ondelete="SET NULL"), index=True
    )  # SET NULL: a deleted webhook keeps the audit row
    user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # NULL for webhook deliveries
    state: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    error: Mapped[str | None] = mapped_column(sa.Text)
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))  # in_app only
