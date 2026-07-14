"""Notifications schema: channels · routes · prefs · events · deliveries.

Revision ID: 20260710_120000
Revises: 20260709_120000
Create Date: 2026-07-10

Design: notifications/_workzone/data-model.html. Five tables: what channels
exist, which type goes where (org matrix), personal narrowing, the event
journal (in-app feed doubles as audit), and per-channel delivery/read state.
Seed: two builtin channels (in_app, email) + the full typexbuiltin route
matrix; in_appxsecurity and in_appxsync are locked open.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260710_120000"
down_revision = "20260709_120000"
branch_labels = None
depends_on = None

_EVENT_TYPES = "('sync', 'security', 'budget', 'system', 'discovery', 'agent', 'account')"


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _updated_at_trigger(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table}_updated_at "
        f"BEFORE UPDATE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("preset", sa.Text),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url_enc", sa.Text),
        sa.Column("secret_enc", sa.Text),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_test_ok", sa.Boolean),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('in_app', 'email', 'webhook')", name="ck_notification_channels_kind"
        ),
        sa.CheckConstraint(
            "preset IS NULL OR preset IN ('slack', 'generic')",
            name="ck_notification_channels_preset",
        ),
        # The in-app rail cannot be switched off — the lock lives on routes.
        sa.CheckConstraint(
            "kind <> 'in_app' OR enabled", name="ck_notification_channels_in_app_on"
        ),
    )
    _updated_at_trigger("notification_channels")

    op.create_table(
        "notification_routes",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column(
            "channel_id",
            sa.BigInteger,
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.CheckConstraint(f"event_type IN {_EVENT_TYPES}", name="ck_notification_routes_type"),
        # A locked cell is locked *open*: it cannot be disabled.
        sa.CheckConstraint("enabled OR NOT locked", name="ck_notification_routes_locked_open"),
        sa.UniqueConstraint("event_type", "channel_id", name="uq_notification_routes_cell"),
    )
    _updated_at_trigger("notification_routes")

    op.create_table(
        "notification_prefs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("in_app_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # No column default on purpose: the INSERT writes the catalog default
        # of the event type (platform types opt-out, personal types opt-in).
        sa.Column("email_enabled", sa.Boolean, nullable=False),
        *_timestamps(),
        sa.CheckConstraint(f"event_type IN {_EVENT_TYPES}", name="ck_notification_prefs_type"),
        sa.UniqueConstraint("user_id", "event_type", name="uq_notification_prefs_cell"),
    )
    _updated_at_trigger("notification_prefs")

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column(
            "target_user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("title", sa.Text, nullable=False),  # i18n key, rendered per reader
        sa.Column("body", sa.Text),
        sa.Column("title_params", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("source", sa.Text),
        sa.Column("source_ref", sa.Text),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("dedup_key", sa.Text, index=True),
        sa.Column("dedup_count", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(f"event_type IN {_EVENT_TYPES}", name="ck_notifications_type"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_notifications_severity"
        ),
    )
    _updated_at_trigger("notifications")

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "notification_id",
            sa.BigInteger,
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # A deleted webhook channel keeps its delivery rows (audit) — SET NULL.
        sa.Column(
            "channel_id",
            sa.BigInteger,
            sa.ForeignKey("notification_channels.id", ondelete="SET NULL"),
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("error", sa.Text),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('queued', 'sent', 'failed', 'read')",
            name="ck_notification_deliveries_state",
        ),
        sa.UniqueConstraint(
            "notification_id", "channel_id", "user_id", name="uq_notification_deliveries_cell"
        ),
    )
    # Webhook deliveries carry no user — NULLs break the composite UNIQUE above.
    op.create_index(
        "uq_notification_deliveries_channel_only",
        "notification_deliveries",
        ["notification_id", "channel_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    _updated_at_trigger("notification_deliveries")

    # --- Seed: builtin channels + the full typexbuiltin route matrix ---
    op.execute(
        "INSERT INTO notification_channels (kind, name, is_builtin) VALUES"
        " ('in_app', 'In-app', true), ('email', 'Email', true);"
    )
    op.execute(
        "INSERT INTO notification_routes (event_type, channel_id, enabled, locked)"
        " SELECT t.event_type, c.id, true,"
        "   (c.kind = 'in_app' AND t.event_type IN ('security', 'sync'))"
        " FROM (VALUES ('sync'), ('security'), ('budget'), ('system'), ('discovery'),"
        "  ('agent'), ('account')) AS t(event_type)"
        " CROSS JOIN notification_channels c WHERE c.is_builtin;"
    )


def downgrade() -> None:
    for table in (
        "notification_deliveries",
        "notifications",
        "notification_prefs",
        "notification_routes",
        "notification_channels",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table};")
        op.drop_table(table)
