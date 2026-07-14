"""Mattermost surface schema: the mattermost_settings singleton.

Revision ID: 20260712_120000
Revises: 20260711_120000
Create Date: 2026-07-12

Design: mattermost/index.html#data — third messenger of the Email SMTP pattern:
a write-only encrypted secret, one `enabled` master switch, availability derived
on the model. Differs from the webhook twins in what the transport dictates: the
server address is a setting (any API-v4-compatible installation) and there is no
webhook secret — the singleton listener dials out, nothing dials in.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260712_120000"
down_revision = "20260711_120000"
branch_labels = None
depends_on = None

_TABLE = "mattermost_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("base_url", sa.Text),
        sa.Column("bot_token_enc", sa.Text),
        sa.Column("bot_user_id", sa.Text),
        sa.Column("bot_username", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_test_ok", sa.Boolean),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_mattermost_settings_singleton"),
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
