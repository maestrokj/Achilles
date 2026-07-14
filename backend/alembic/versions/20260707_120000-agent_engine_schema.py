"""Agent Engine schema: agents, agent_runs, agent_tools + platform run knobs.

Revision ID: 20260707_120000
Revises: 20260706_120000
Create Date: 2026-07-07

Design: agent-engine/_workzone/data-model.html + governance.html.
agents carries two independent locks (owner enabled + sticky admin_paused);
agent_runs is a run journal (created_at only, progress in domain timestamps)
with a per-agent single-flight lock; agent_tools is the append-only selection
of optional tools on top of the locked KS core (which never hits the table).
The weekly budget is derived (SUM over agent_runs.tokens_used), its ceiling
and the run limits live in platform_settings.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260707_120000"
down_revision = "20260706_120000"
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


_PLATFORM_COLUMNS = (
    # NULL = no ceiling; checked before a run starts (governance.html#budget).
    sa.Column("agent_weekly_token_budget", sa.BigInteger),
    sa.Column("agent_iteration_cap", sa.Integer, nullable=False, server_default=sa.text("15")),
    sa.Column("agent_max_concurrency", sa.Integer, nullable=False, server_default=sa.text("4")),
)

_PLATFORM_CHECKS = (
    (
        "ck_platform_settings_agent_budget",
        "agent_weekly_token_budget IS NULL OR agent_weekly_token_budget > 0",
    ),
    ("ck_platform_settings_agent_iteration_cap", "agent_iteration_cap > 0"),
    ("ck_platform_settings_agent_concurrency", "agent_max_concurrency > 0"),
)


def upgrade() -> None:
    for column in _PLATFORM_COLUMNS:
        op.add_column("platform_settings", column)
    for name, condition in _PLATFORM_CHECKS:
        op.create_check_constraint(name, "platform_settings", condition)

    op.create_table(
        "agents",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("schedule", postgresql.JSONB),
        sa.Column(
            "model_id",
            sa.BigInteger,
            sa.ForeignKey("agent_models.id", ondelete="SET NULL"),
            index=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("admin_paused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), index=True),
        sa.Column("meta", postgresql.JSONB),
        *_timestamps(),
    )
    _add_updated_at_trigger("agents")

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "agent_id",
            sa.BigInteger,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("reason", sa.Text),
        sa.Column("output", sa.Text),
        sa.Column("tokens_used", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True), index=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("\"trigger\" IN ('manual','scheduled')", name="ck_agent_runs_trigger"),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','skipped')",
            name="ck_agent_runs_state",
        ),
        sa.CheckConstraint(
            "reason IN ('budget_exceeded','already_running','iteration_cap','error','stale')",
            name="ck_agent_runs_reason",
        ),
    )
    op.create_index(
        "uq_agent_runs_active",
        "agent_runs",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued','running')"),
    )
    op.create_index("ix_agent_runs_agent_id_id", "agent_runs", ["agent_id", "id"])

    op.create_table(
        "agent_tools",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "agent_id",
            sa.BigInteger,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_id",
            sa.BigInteger,
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("agent_id", "tool_id", name="uq_agent_tools_pair"),
    )


def downgrade() -> None:
    op.drop_table("agent_tools")
    op.drop_index("ix_agent_runs_agent_id_id", table_name="agent_runs")
    op.drop_index("uq_agent_runs_active", table_name="agent_runs")
    op.drop_table("agent_runs")
    _drop_updated_at_trigger("agents")
    op.drop_table("agents")
    for name, _ in reversed(_PLATFORM_CHECKS):
        op.drop_constraint(name, "platform_settings", type_="check")
    for column in reversed(_PLATFORM_COLUMNS):
        op.drop_column("platform_settings", column.name)
