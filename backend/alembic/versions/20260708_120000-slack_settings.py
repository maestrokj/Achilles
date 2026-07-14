"""Slack surface schema: the slack_settings singleton.

Revision ID: 20260708_120000
Revises: 20260707_120000
Create Date: 2026-07-08

Design: slack/index.html#data — mirror of the Email SMTP pattern: write-only
encrypted secrets, one `enabled` master switch, availability derived on the
model, workspace facts (team/team_name/bot_user_id) stamped by the live test.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260708_120000"
down_revision = "20260707_120000"
branch_labels = None
depends_on = None

_TABLE = "slack_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("team", sa.Text),
        sa.Column("team_name", sa.Text),
        sa.Column("bot_token_enc", sa.Text),
        sa.Column("signing_secret_enc", sa.Text),
        sa.Column("bot_user_id", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("auto_link_by_email", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_test_ok", sa.Boolean),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_slack_settings_singleton"),
    )
    op.execute(
        f"CREATE TRIGGER trg_{_TABLE}_updated_at "
        f"BEFORE UPDATE ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )
    op.execute(f"INSERT INTO {_TABLE} (id) VALUES (1);")  # noqa: S608 — _TABLE is a constant


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_TABLE}_updated_at ON {_TABLE};")
    op.drop_table(_TABLE)
