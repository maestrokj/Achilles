"""AI Foundation schema: providers, model catalog, assignments, usage, tools, prompt.

Revision ID: 20260703_120000
Revises: 20260702_140000
Create Date: 2026-07-03

Design: docs/architecture/modules/ai-foundation/_workzone/data-model.html.
Seeds the same step: the Platform provider (is_system, the built-in embeddings
runtime) + its builtin model catalog (enabled, weights load lazily on
assignment) + the two v1 tool presets (disabled) + the prompt_settings
singleton. No default model assignment on purpose — picking the embedding
model fixes chunks.embedding halfvec(N) in stage 4, an Admin must choose.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260703_120000"
down_revision = "20260702_140000"
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


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column[datetime]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _model_fk(*, ondelete: str, nullable: bool = False, unique: bool = False) -> sa.Column[int]:
    return sa.Column(
        "model_id",
        sa.BigInteger,
        sa.ForeignKey("ai_models.id", ondelete=ondelete),
        nullable=nullable,
        unique=unique,
    )


_TABLES = (
    "prompt_settings",
    "tools",
    "model_usage",
    "agent_models",
    "chat_models",
    "model_assignments",
    "ai_models",
    "ai_providers",
)


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False, server_default=sa.text("'cloud'")),
        sa.Column("adapter", sa.Text, nullable=False),
        sa.Column("base_url", sa.Text),
        sa.Column("api_key_enc", sa.Text),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'unchecked'")),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("kind IN ('cloud','local','platform')", name="ck_ai_providers_kind"),
        sa.CheckConstraint(
            "adapter IN ('openai','anthropic','google','ollama','openai_compatible')",
            name="ck_ai_providers_adapter",
        ),
        sa.CheckConstraint(
            "status IN ('active','error','unchecked')", name="ck_ai_providers_status"
        ),
        sa.CheckConstraint(
            "kind = 'cloud' OR base_url IS NOT NULL", name="ck_ai_providers_base_url"
        ),
    )
    _add_updated_at_trigger("ai_providers")
    # The seeded system provider is the built-in runtime; even a raw DELETE
    # (past the service layer) must bounce — the lock lives in the DB.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_system_provider_delete() RETURNS trigger AS $$
        BEGIN
            IF OLD.is_system THEN
                RAISE EXCEPTION 'system provider is protected';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_ai_providers_system_lock "
        "BEFORE DELETE ON ai_providers "
        "FOR EACH ROW EXECUTE FUNCTION forbid_system_provider_delete();"
    )

    op.create_table(
        "ai_models",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "provider_id",
            sa.BigInteger,
            sa.ForeignKey("ai_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("model_type", sa.Text, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("price_input", sa.Numeric),
        sa.Column("price_output", sa.Numeric),
        sa.Column("meta", postgresql.JSONB),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_ai_models_provider_model"),
        sa.CheckConstraint("model_type IN ('chat','embedding')", name="ck_ai_models_type"),
        sa.CheckConstraint(
            "origin IN ('discovered','manual','builtin')", name="ck_ai_models_origin"
        ),
    )
    _add_updated_at_trigger("ai_models")

    op.create_table(
        "model_assignments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("function", sa.Text, nullable=False, unique=True),
        _model_fk(ondelete="RESTRICT", nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "function IN ('harvester_embedding')",
            name="ck_model_assignments_function",
        ),
    )
    op.create_index("ix_model_assignments_model_id", "model_assignments", ["model_id"])
    _add_updated_at_trigger("model_assignments")

    for table in ("chat_models", "agent_models"):
        op.create_table(
            table,
            sa.Column("id", sa.BigInteger, primary_key=True),
            _model_fk(ondelete="RESTRICT", unique=True),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
            # A model can sit in the allow-list yet be paused: the toggle takes
            # it off the surface without losing its place (or the default mark).
            sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            _created_at(),
            _updated_at(),
        )
        op.create_index(
            f"uq_{table}_default",
            table,
            ["is_default"],
            unique=True,
            postgresql_where=sa.text("is_default"),
        )
        _add_updated_at_trigger(table)

    op.create_table(
        "model_usage",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _model_fk(ondelete="SET NULL", nullable=True),
        sa.Column("function", sa.Text, nullable=False),
        sa.Column("bucket_date", sa.Date, nullable=False),
        sa.Column("request_count", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("cost", sa.Numeric),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("model_id", "function", "bucket_date", name="uq_model_usage_bucket"),
        sa.CheckConstraint(
            "function IN ('harvester_embedding','query_rag','agent_engine','chat')",
            name="ck_model_usage_function",
        ),
    )
    _add_updated_at_trigger("model_usage")

    op.create_table(
        "tools",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("source", sa.Text, nullable=False, server_default=sa.text("'preset'")),
        sa.Column("access", sa.Text, nullable=False, server_default=sa.text("'read_only'")),
        sa.Column("config", postgresql.JSONB),
        sa.Column("credential_enc", sa.Text),
        sa.Column("chat_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("agents_allowed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'unchecked'")),
        sa.Column("last_check_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("source IN ('preset','custom','mcp','openapi')", name="ck_tools_source"),
        sa.CheckConstraint("access IN ('read_only','write')", name="ck_tools_access"),
        sa.CheckConstraint("status IN ('active','error','unchecked')", name="ck_tools_status"),
    )
    _add_updated_at_trigger("tools")

    op.create_table(
        "prompt_settings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("safety_text", sa.Text),
        sa.Column("org_text", sa.Text),
        sa.Column("updated_by", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("id = 1", name="ck_prompt_settings_singleton"),
    )
    _add_updated_at_trigger("prompt_settings")

    # --- Seed (same logical step; literals are frozen on purpose) ---

    op.execute(
        """
        INSERT INTO ai_providers (name, kind, adapter, base_url, is_system)
        VALUES ('Platform', 'platform', 'openai_compatible', 'http://embeddings:80', true);
        """
    )
    op.execute(
        """
        INSERT INTO ai_models
            (provider_id, model_id, display_name, model_type, origin, is_enabled, meta)
        SELECT p.id, m.model_id, m.display_name, 'embedding', 'builtin', true, m.meta::jsonb
        FROM ai_providers p,
             -- approx_size_bytes: rough fp16 weight footprint (params x 2), the
             -- size the runtime actually loads; the picker surfaces it so the
             -- admin gauges memory pressure. Cloud models carry no size.
             (VALUES
              ('BAAI/bge-m3', 'BGE-M3',
             '{"embedding_dim": 1024, "max_input_tokens": 8192, "approx_size_bytes": 1135509504}'),
              ('Qwen/Qwen3-Embedding-0.6B', 'Qwen3 Embedding 0.6B',
             '{"embedding_dim": 1024, "max_input_tokens": 32768, "approx_size_bytes": 1191586416}')
             ) AS m(model_id, display_name, meta)
        WHERE p.is_system;
        """
    )
    op.execute(
        """
        INSERT INTO tools (name, source, access)
        VALUES ('web_search', 'preset', 'read_only'),
               ('fetch_url', 'preset', 'read_only');
        """
    )
    op.execute("INSERT INTO prompt_settings (id) VALUES (1);")


def downgrade() -> None:
    for table in _TABLES:
        _drop_updated_at_trigger(table)
    op.execute("DROP TRIGGER IF EXISTS trg_ai_providers_system_lock ON ai_providers;")
    op.execute("DROP FUNCTION IF EXISTS forbid_system_provider_delete();")
    # The system-provider guard is gone with its trigger; plain drops cascade
    # the seed rows with the tables.
    for table in _TABLES:
        op.drop_table(table)
