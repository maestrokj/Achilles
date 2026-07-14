"""Auth & Security schema: users, tokens, api_keys, identity_mapping, audit_log.

Revision ID: 20260702_120000
Revises: 20260517_195200
Create Date: 2026-07-02

Design: docs/architecture/modules/auth-security/_workzone/data-model.html.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260702_120000"
down_revision = "20260517_195200"
branch_labels = None
depends_on = None

# audit_log is append-only at the DB level, not just by convention.
AUDIT_APPEND_ONLY_TRIGGER = """
CREATE OR REPLACE FUNCTION audit_log_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
"""


def _add_updated_at_trigger(table_name: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table_name}_updated_at "
        f"BEFORE UPDATE ON {table_name} "
        f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def _drop_updated_at_trigger(table_name: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_updated_at ON {table_name};")


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column[datetime]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _user_fk(name: str = "user_id") -> sa.Column[int]:
    return sa.Column(
        name,
        sa.BigInteger,
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("auth_provider", sa.Text, nullable=False, server_default=sa.text("'local'")),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.Text),
        sa.Column("locale", sa.Text),
        sa.Column("date_format", sa.Text),
        sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("mfa_secret", sa.Text),
        sa.Column("mfa_recovery", postgresql.JSONB),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("role IN ('owner','admin','member')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('active','deactivated')", name="ck_users_status"),
        sa.CheckConstraint(
            "auth_provider IN ('local','okta','azure_ad')", name="ck_users_auth_provider"
        ),
        sa.CheckConstraint(
            "(auth_provider = 'local') = (password_hash IS NOT NULL)",
            name="ck_users_local_needs_password",
        ),
        sa.CheckConstraint("locale IN ('ru','en')", name="ck_users_locale"),
        sa.CheckConstraint(
            "date_format IN ('DD.MM.YYYY','MM/DD/YYYY','YYYY-MM-DD')",
            name="ck_users_date_format",
        ),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    _add_updated_at_trigger("users")

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("is_revoked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remember_me", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("user_agent", sa.Text),
        sa.Column("ip", sa.Text),
        _created_at(),
    )

    op.create_table(
        "invite_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        _user_fk("invited_by"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint("role IN ('owner','admin','member')", name="ck_invite_tokens_role"),
    )

    op.create_table(
        "reset_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        _created_at(),
    )

    op.create_table(
        "link_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(),
        sa.Column("code_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        _created_at(),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(),
        sa.Column("key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("prefix", sa.Text, nullable=False, index=True),
        sa.Column("name", sa.Text),  # optional owner-facing label; NULL → show prefix
        sa.Column("scope", postgresql.JSONB, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("is_revoked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
    )
    _add_updated_at_trigger("api_keys")

    op.create_table(
        "identity_mapping",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_user_id", sa.Text, nullable=False),
        sa.Column("source_email", sa.Text),
        _created_at(),
        sa.UniqueConstraint("source", "source_user_id", name="uq_identity_mapping_source_user"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("actor_id", sa.BigInteger, index=True),  # no FK: entries outlive the actor
        # Actor email captured at write time, so a deleted actor still reads by name.
        sa.Column("actor_email", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text),
        sa.Column("target_id", sa.Text),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("ip", sa.Text),
        sa.Column("user_agent", sa.Text),
        sa.Column("meta", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.CheckConstraint("result IN ('success','failure')", name="ck_audit_log_result"),
    )
    op.execute(AUDIT_APPEND_ONLY_TRIGGER)
    op.execute(
        "CREATE TRIGGER trg_audit_log_append_only "
        "BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only();")
    op.drop_table("audit_log")
    op.drop_table("identity_mapping")
    _drop_updated_at_trigger("api_keys")
    op.drop_table("api_keys")
    op.drop_table("link_tokens")
    op.drop_table("reset_tokens")
    op.drop_table("invite_tokens")
    op.drop_table("refresh_tokens")
    _drop_updated_at_trigger("users")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
