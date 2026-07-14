"""Query Engine schema: conversations, messages, retrieval_trace, access_counter.

Revision ID: 20260705_120000
Revises: 20260704_120000
Create Date: 2026-07-05

Design: query-engine/_workzone/data-model.html. KS is referenced by bare ids
(entity_ref, citations JSONB) — links across the boundary, never FKs.
messages carries updated_at only because feedback mutates in place;
retrieval_trace is immutable (created_at only).
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260705_120000"
down_revision = "20260704_120000"
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


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("surface", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("selected_model", sa.Text),
        sa.Column("meta", postgresql.JSONB),
        *_timestamps(),
        sa.CheckConstraint(
            "surface IN ('web', 'slack', 'telegram', 'mattermost', 'mcp', 'extension')",
            name="ck_conversations_surface",
        ),
    )
    _add_updated_at_trigger("conversations")

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("tokens_used", sa.BigInteger),
        sa.Column("feedback", sa.SmallInteger),
        # Terminal outcome (NULL = completed) + the reason when it is 'failed';
        # a broken turn persists its marker so a reload replays the notice.
        sa.Column("finish", sa.Text),
        sa.Column("error_code", sa.Text),
        *_timestamps(),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint("feedback IN (-1, 1)", name="ck_messages_feedback"),
        sa.CheckConstraint("finish IN ('stopped', 'failed')", name="ck_messages_finish"),
    )
    _add_updated_at_trigger("messages")

    op.create_table(
        "retrieval_trace",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "message_id",
            sa.BigInteger,
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("search_query", sa.Text, nullable=False),
        sa.Column("candidates", postgresql.JSONB),
        sa.Column("citations", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "access_counter",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_ref", sa.BigInteger, nullable=False, unique=True),
        sa.Column("hits", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    _add_updated_at_trigger("access_counter")


def downgrade() -> None:
    for table in ("access_counter", "retrieval_trace", "messages", "conversations"):
        _drop_updated_at_trigger(table)
        op.drop_table(table)
