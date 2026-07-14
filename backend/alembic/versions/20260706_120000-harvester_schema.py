"""Harvester schema: sources extension, sync_runs, dead_letters, platform_settings.

Revision ID: 20260706_120000
Revises: 20260705_120000
Create Date: 2026-07-06

Design: harvester/_workzone/data-model.html + sync-modes.html#scheduling.
sync_runs is a run journal (created_at only, progress in domain timestamps)
with a per-source single-flight lock; dead_letters is a work queue (resolved
rows are deleted), one row per item. platform_settings is a seeded singleton.
curation_runs gains destructive_since — the lane-coordination window
(knowledge-store/lifecycle.html#coordination).
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260706_120000"
down_revision = "20260705_120000"
branch_labels = None
depends_on = None


def _add_updated_at_trigger(table_name: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table_name}_updated_at "
        f"BEFORE UPDATE ON {table_name} "
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def _drop_updated_at_trigger(table_name: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_updated_at ON {table_name};")


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


_SOURCES_COLUMNS = (
    sa.Column("base_url", sa.Text),
    sa.Column("auth_account", sa.Text, nullable=False, server_default=sa.text("'service'")),
    sa.Column("auth_method", sa.Text, nullable=False, server_default=sa.text("'static_token'")),
    sa.Column("credential_enc", sa.Text),
    sa.Column("scope_mode", sa.Text, nullable=False, server_default=sa.text("'all'")),
    sa.Column("scope_list", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
    sa.Column("content_filters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'")),
    sa.Column("sync_interval", sa.Integer),
    sa.Column("reconcile_interval", sa.Integer),
    sa.Column("reconcile_window", sa.Integer),
    sa.Column("webhook_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("webhook_secret_enc", sa.Text),
    sa.Column("incremental_cursor", postgresql.JSONB),
    sa.Column("last_probe_at", sa.DateTime(timezone=True)),
    sa.Column("last_probe_status", sa.Text),
)

_SOURCES_CHECKS = (
    ("ck_sources_auth_account", "auth_account IN ('service','personal')"),
    ("ck_sources_auth_method", "auth_method IN ('static_token','oauth')"),
    ("ck_sources_scope_mode", "scope_mode IN ('all','selected')"),
    ("ck_sources_last_probe_status", "last_probe_status IN ('ok','unreachable','auth_failed')"),
)


def upgrade() -> None:
    for column in _SOURCES_COLUMNS:
        op.add_column("sources", column)
    for name, condition in _SOURCES_CHECKS:
        op.create_check_constraint(name, "sources", condition)

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("scope", postgresql.JSONB),
        sa.Column("entities_done", sa.Integer),
        sa.Column("entities_total", sa.Integer),
        sa.Column("checkpoint", postgresql.JSONB),
        sa.Column("error_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_detail", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "mode IN ('full','incremental','reconciliation')", name="ck_sync_runs_mode"
        ),
        sa.CheckConstraint(
            "\"trigger\" IN ('connect','schedule','webhook','watchdog','manual')",
            name="ck_sync_runs_trigger",
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_sync_runs_state",
        ),
    )
    op.create_index(
        "uq_sync_runs_active",
        "sync_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.create_index("ix_sync_runs_source_created", "sync_runs", ["source_id", "created_at"])

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.BigInteger,
            sa.ForeignKey("sync_runs.id", ondelete="SET NULL"),
            index=True,
        ),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_entity_id", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("error_detail", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint(
            "source_id", "source_type", "source_entity_id", name="uq_dead_letters_item"
        ),
        sa.CheckConstraint(
            "reason IN ('permission','not_found','malformed','rate_limited','unknown')",
            name="ck_dead_letters_reason",
        ),
    )
    _add_updated_at_trigger("dead_letters")

    op.create_table(
        "platform_settings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # Organization & defaults — admin-panel/_workzone/data-model.html
        sa.Column("org_name", sa.Text, nullable=False, server_default=sa.text("'Achilles'")),
        sa.Column("org_logo_url", sa.Text),
        sa.Column("org_description", sa.Text),
        sa.Column("accent_color", sa.Text, nullable=False, server_default=sa.text("'#6366f1'")),
        sa.Column("timezone", sa.Text, nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("locale", sa.Text, nullable=False, server_default=sa.text("'ru'")),
        sa.Column("date_format", sa.Text, nullable=False, server_default=sa.text("'DD.MM.YYYY'")),
        # Session TTLs (seconds) — override the auth defaults
        sa.Column("access_token_ttl", sa.Integer, nullable=False, server_default=sa.text("900")),
        sa.Column(
            "refresh_token_ttl", sa.Integer, nullable=False, server_default=sa.text("2592000")
        ),
        sa.Column(
            "session_absolute_ttl", sa.Integer, nullable=False, server_default=sa.text("7776000")
        ),
        # Integrations & maintenance
        sa.Column("maintenance_mode", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("mcp_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # AI budgets — thresholds live here, the ledgers live in AI Foundation
        sa.Column("ai_monthly_budget", sa.Numeric),
        sa.Column(
            "ai_budget_alert_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("chat_weekly_token_budget", sa.BigInteger),
        # Harvester scheduling knobs
        sa.Column(
            "sync_interval_minutes", sa.Integer, nullable=False, server_default=sa.text("15")
        ),
        sa.Column(
            "reconcile_minute_of_week", sa.Integer, nullable=False, server_default=sa.text("8820")
        ),
        sa.Column(
            "watchdog_silence_hours", sa.Integer, nullable=False, server_default=sa.text("12")
        ),
        # Curation Pass window — org-local, mirrors backup_settings cadence
        sa.Column("curation_frequency", sa.Text, nullable=False, server_default=sa.text("'daily'")),
        sa.Column("curation_weekday", sa.Integer),
        sa.Column("curation_time", sa.Text, nullable=False, server_default=sa.text("'04:00'")),
        *_timestamps(),
        sa.CheckConstraint("id = 1", name="ck_platform_settings_singleton"),
        sa.CheckConstraint(
            "accent_color ~ '^#[0-9a-fA-F]{6}$'", name="ck_platform_settings_accent_color"
        ),
        sa.CheckConstraint("locale IN ('ru', 'en')", name="ck_platform_settings_locale"),
        sa.CheckConstraint(
            "date_format IN ('DD.MM.YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD')",
            name="ck_platform_settings_date_format",
        ),
        sa.CheckConstraint(
            "access_token_ttl BETWEEN 1 AND 3600", name="ck_platform_settings_access_ttl"
        ),
        sa.CheckConstraint("refresh_token_ttl > 0", name="ck_platform_settings_refresh_ttl"),
        sa.CheckConstraint("session_absolute_ttl > 0", name="ck_platform_settings_absolute_ttl"),
        sa.CheckConstraint(
            "access_token_ttl <= refresh_token_ttl AND refresh_token_ttl <= session_absolute_ttl",
            name="ck_platform_settings_ttl_order",
        ),
        sa.CheckConstraint(
            "NOT ai_budget_alert_enabled OR ai_monthly_budget IS NOT NULL",
            name="ck_platform_settings_budget_alert",
        ),
        sa.CheckConstraint(
            "chat_weekly_token_budget IS NULL OR chat_weekly_token_budget > 0",
            name="ck_platform_settings_chat_budget",
        ),
        sa.CheckConstraint("sync_interval_minutes > 0", name="ck_platform_settings_sync_interval"),
        sa.CheckConstraint(
            "reconcile_minute_of_week BETWEEN 0 AND 10079",
            name="ck_platform_settings_reconcile_window",
        ),
        sa.CheckConstraint("watchdog_silence_hours > 0", name="ck_platform_settings_watchdog"),
        sa.CheckConstraint(
            "curation_frequency IN ('daily', 'weekly')",
            name="ck_platform_settings_curation_frequency",
        ),
        sa.CheckConstraint(
            "curation_weekday IS NULL OR curation_weekday BETWEEN 0 AND 6",
            name="ck_platform_settings_curation_weekday",
        ),
        sa.CheckConstraint(
            "curation_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'",
            name="ck_platform_settings_curation_time",
        ),
    )
    _add_updated_at_trigger("platform_settings")
    op.execute("INSERT INTO platform_settings (id) VALUES (1)")

    op.add_column("curation_runs", sa.Column("destructive_since", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("curation_runs", "destructive_since")
    _drop_updated_at_trigger("platform_settings")
    op.drop_table("platform_settings")
    _drop_updated_at_trigger("dead_letters")
    op.drop_table("dead_letters")
    op.drop_index("ix_sync_runs_source_created", table_name="sync_runs")
    op.drop_index("uq_sync_runs_active", table_name="sync_runs")
    op.drop_table("sync_runs")
    for name, _ in reversed(_SOURCES_CHECKS):
        op.drop_constraint(name, "sources", type_="check")
    for column in reversed(_SOURCES_COLUMNS):
        op.drop_column("sources", column.name)
