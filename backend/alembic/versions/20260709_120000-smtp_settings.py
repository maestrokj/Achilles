"""Email transport schema: the smtp_settings singleton.

Revision ID: 20260709_120000
Revises: 20260708_120000
Create Date: 2026-07-09

Design: email/_workzone/data-model.html — the same singleton pattern as
slack_settings: write-only encrypted password, one `is_enabled` master switch,
availability derived on the model, last_test_* stamped by the inline probe.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260709_120000"
down_revision = "20260708_120000"
branch_labels = None
depends_on = None

_TABLE = "smtp_settings"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("host", sa.Text),
        sa.Column("port", sa.Integer),
        sa.Column("security", sa.Text, nullable=False, server_default=sa.text("'starttls'")),
        sa.Column("username", sa.Text),
        sa.Column("password_enc", sa.Text),
        sa.Column("from_address", sa.Text),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_test_ok", sa.Boolean),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),
        sa.CheckConstraint(
            "security IN ('none', 'starttls', 'ssl_tls')", name="ck_smtp_settings_security"
        ),
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
