"""AI Foundation data model — ai-foundation/_workzone/data-model.html.

Cross-cutting AI registry: provider slots → model catalog → assignments/
lists/usage → tools + the prompt singleton. Consumers (Harvester, KS, Query
Engine, Agent Engine) only read from here — no cycles. Conventions follow
the schema-wide set: BigInteger PK/FK · Text + CHECK instead of native ENUM ·
TIMESTAMPTZ. An FK whose composite UNIQUE/index leads with it carries no
extra single-column index.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from achilles.ai_foundation.constants import (
    SYSTEM_FUNCTIONS,
    AiFunction,
    CheckStatus,
    ModelOrigin,
    ModelType,
    ProviderAdapter,
    ProviderKind,
    ToolAccess,
    ToolSource,
)
from achilles.db.base import Base, TimestampMixin, enum_check

_SYSTEM_FUNCTIONS_SQL = ",".join(f"'{f}'" for f in SYSTEM_FUNCTIONS)


class AiProvider(TimestampMixin, Base):
    """Provider slot: where models run and how to talk to them.

    The seeded Platform row (is_system) is the built-in embeddings runtime;
    a DB trigger (migration) blocks its DELETE even past the service layer.
    """

    __tablename__ = "ai_providers"
    __table_args__ = (
        sa.CheckConstraint(f"kind IN ({enum_check(ProviderKind)})", name="ck_ai_providers_kind"),
        sa.CheckConstraint(
            f"adapter IN ({enum_check(ProviderAdapter)})", name="ck_ai_providers_adapter"
        ),
        sa.CheckConstraint(f"status IN ({enum_check(CheckStatus)})", name="ck_ai_providers_status"),
        # A local runtime is unreachable without an address; cloud has a known one.
        sa.CheckConstraint(
            "kind = 'cloud' OR base_url IS NOT NULL", name="ck_ai_providers_base_url"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{ProviderKind.CLOUD}'")
    )
    adapter: Mapped[str] = mapped_column(sa.Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(sa.Text)  # NULL → adapter's default host
    # Write-only, AES-256-GCM (crypto core); API returns the ••••xxxx mask only.
    api_key_enc: Mapped[str | None] = mapped_column(sa.Text)  # NULL → no key needed
    is_system: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{CheckStatus.UNCHECKED}'")
    )
    last_check_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class AiModel(TimestampMixin, Base):
    """Catalog row: one model as one provider serves it.

    meta carries model intrinsics the consumers read instead of hardcoding:
    embedding_dim (KS provisions halfvec(N)), max_input_tokens (chunker
    ceiling), instruction_prefix.
    """

    __tablename__ = "ai_models"
    __table_args__ = (
        sa.UniqueConstraint("provider_id", "model_id", name="uq_ai_models_provider_model"),
        sa.CheckConstraint(f"model_type IN ({enum_check(ModelType)})", name="ck_ai_models_type"),
        sa.CheckConstraint(f"origin IN ({enum_check(ModelOrigin)})", name="ck_ai_models_origin"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(sa.Text, nullable=False)  # provider-side identifier
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    origin: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    # NULL for embedding models and local runtimes (no per-token price).
    price_input: Mapped[Decimal | None] = mapped_column(sa.Numeric)  # $ per 1M input tokens
    price_output: Mapped[Decimal | None] = mapped_column(sa.Numeric)  # $ per 1M output tokens
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ModelAssignment(TimestampMixin, Base):
    """System function → model, one row per function (data-model.html#t-model-assignments).

    Only the system subset of the functions dictionary lives here; chat and
    agents resolve through chat_models/agent_models. No default assignment is
    seeded on purpose: picking harvester_embedding fixes chunks.embedding
    halfvec(N), so an Admin must choose before the first ingest.
    """

    __tablename__ = "model_assignments"
    __table_args__ = (
        sa.CheckConstraint(
            f"function IN ({_SYSTEM_FUNCTIONS_SQL})", name="ck_model_assignments_function"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    function: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    model_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("ai_models.id", ondelete="RESTRICT"), index=True
    )  # NULL → function waits for its module


class ChatModel(TimestampMixin, Base):
    """Models offered in the user chat picker; exactly one is the default."""

    __tablename__ = "chat_models"
    __table_args__ = (
        sa.Index(
            "uq_chat_models_default",
            "is_default",
            unique=True,
            postgresql_where=sa.text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    is_default: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    # Paused-in-place: stays in the list (and may keep the default mark) but is
    # not offered on the surface — distinct from removal, which frees the row.
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class AgentModel(TimestampMixin, Base):
    """Models agents may pick from (chat-type, tool-capable); same shape as chat_models.

    Agent Engine's agents.model_id (stage 6) references this table, not
    ai_models — removing a model from the list is what stops agents.
    """

    __tablename__ = "agent_models"
    __table_args__ = (
        sa.Index(
            "uq_agent_models_default",
            "is_default",
            unique=True,
            postgresql_where=sa.text("is_default"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    is_default: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    # See ChatModel.is_enabled — same pause-vs-remove split for agents.
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class ModelUsage(TimestampMixin, Base):
    """Daily spend aggregate per (model · function) — cost-accounting.html.

    Money/company-total slice; per-person attribution lives in the journals
    (messages.tokens_used, agent_runs.tokens_used). Rows mutate via upsert
    increments. model_id survives model deletion (SET NULL) — spend history
    outlives the catalog row.
    """

    __tablename__ = "model_usage"
    __table_args__ = (
        sa.UniqueConstraint("model_id", "function", "bucket_date", name="uq_model_usage_bucket"),
        sa.CheckConstraint(
            f"function IN ({enum_check(AiFunction)})", name="ck_model_usage_function"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    model_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("ai_models.id", ondelete="SET NULL")
    )
    function: Mapped[str] = mapped_column(sa.Text, nullable=False)
    bucket_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    request_count: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )
    input_tokens: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )
    cost: Mapped[Decimal | None] = mapped_column(sa.Numeric)  # NULL → prices unknown


class Tool(TimestampMixin, Base):
    """Tool instance: a catalog row over a registered code type (tool-catalog.html).

    name is the join key to the code registry; display strings live in i18n,
    not the DB. config is the non-secret part; the secret goes to
    credential_enc (write-only, API exposes the is_set flag only).
    """

    __tablename__ = "tools"
    __table_args__ = (
        sa.CheckConstraint(f"source IN ({enum_check(ToolSource)})", name="ck_tools_source"),
        sa.CheckConstraint(f"access IN ({enum_check(ToolAccess)})", name="ck_tools_access"),
        sa.CheckConstraint(f"status IN ({enum_check(CheckStatus)})", name="ck_tools_status"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{ToolSource.PRESET}'")
    )
    access: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{ToolAccess.READ_ONLY}'")
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    credential_enc: Mapped[str | None] = mapped_column(sa.Text)  # NULL → no secret needed
    chat_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    agents_allowed: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{CheckStatus.UNCHECKED}'")
    )
    last_check_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class PromptSettings(TimestampMixin, Base):
    """Singleton (id=1, seeded by the migration); the app reads/updates, never inserts.

    NULL column → the built-in locale default from code (prompt_texts.py);
    non-NULL → the admin's frozen override. "Reset" nulls the column.
    """

    __tablename__ = "prompt_settings"
    __table_args__ = (sa.CheckConstraint("id = 1", name="ck_prompt_settings_singleton"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    safety_text: Mapped[str | None] = mapped_column(sa.Text)
    org_text: Mapped[str | None] = mapped_column(sa.Text)
    updated_by: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
