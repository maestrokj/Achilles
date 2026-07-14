"""Knowledge Store data model — data-model.html, acl-identity.html, lifecycle.html.

One entity = one row with projections in one Postgres: relational body
(entities) · text fragments (chunks) · graph (entity_edge). Conventions:
BigInteger PK/FK · Text + CHECK instead of native ENUM · TIMESTAMPTZ ·
immutable rows carry created_at only. An FK whose composite UNIQUE/index leads
with it carries no extra single-column index (pure write amplification).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.auth.constants import ACCESS_TOKEN_TTL_MAX
from achilles.db.base import Base, TimestampMixin, enum_check
from achilles.knowledge_store.constants import (
    ACCENT_COLOR_PATTERN,
    FTS_CONFIG,
    WINDOW_TIME_PATTERN,
    AclScope,
    AuthAccount,
    AuthMethod,
    AuthorityTier,
    BackupState,
    CadenceFrequency,
    CurationState,
    CurationTrigger,
    DateFormat,
    EdgeOrigin,
    EntityStatus,
    PlatformLocale,
    ProbeStatus,
    RelType,
    SourceScopeMode,
    SourceState,
)


class Source(TimestampMixin, Base):
    """Connected source — control layer (harvester/data-model.html#sources-table).

    The table lives in KS (its FK owners and trust decay are here), but the
    extended columns are written by Harvester services only. `state` stores the
    admin's intent; health (idle/syncing/error) is derived from the last run +
    probe, never stored.
    """

    __tablename__ = "sources"
    __table_args__ = (
        sa.CheckConstraint(f"state IN ({enum_check(SourceState)})", name="ck_sources_state"),
        sa.CheckConstraint(
            f"authority_tier IN ({enum_check(AuthorityTier)})", name="ck_sources_authority_tier"
        ),
        sa.CheckConstraint(
            f"auth_account IN ({enum_check(AuthAccount)})", name="ck_sources_auth_account"
        ),
        sa.CheckConstraint(
            f"auth_method IN ({enum_check(AuthMethod)})", name="ck_sources_auth_method"
        ),
        sa.CheckConstraint(
            f"scope_mode IN ({enum_check(SourceScopeMode)})", name="ck_sources_scope_mode"
        ),
        sa.CheckConstraint(
            f"last_probe_status IN ({enum_check(ProbeStatus)})",
            name="ck_sources_last_probe_status",
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    connector_type: Mapped[str] = mapped_column(sa.Text, nullable=False)  # manifest validates
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{SourceState.ACTIVE}'")
    )
    authority_tier: Mapped[str | None] = mapped_column(
        sa.Text
    )  # decay input; NULL = manifest default_authority
    base_url: Mapped[str | None] = mapped_column(sa.Text)  # NULL for fixed-API sources (Slack)
    auth_account: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{AuthAccount.SERVICE}'")
    )
    auth_method: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{AuthMethod.STATIC_TOKEN}'")
    )
    credential_enc: Mapped[str | None] = mapped_column(sa.Text)  # AES-256-GCM; NULL = disconnected
    scope_mode: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{SourceScopeMode.ALL}'")
    )
    scope_list: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )  # deny-list (all) / allow-list (selected)
    content_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )  # collection toggles on top of the scope rules
    sync_interval: Mapped[int | None] = mapped_column(sa.Integer)  # minutes; NULL = inherit
    reconcile_interval: Mapped[int | None] = mapped_column(sa.Integer)  # days; NULL = inherit
    reconcile_window: Mapped[int | None] = mapped_column(
        sa.Integer
    )  # minute-of-week, org tz; NULL = inherit
    webhook_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )  # real-time channel; intake at harvester/routes/sources.py (security.html#webhooks)
    webhook_secret_enc: Mapped[str | None] = mapped_column(sa.Text)
    incremental_cursor: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB
    )  # `since` of the last successful run; NULL until the first
    last_probe_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_probe_status: Mapped[str | None] = mapped_column(sa.Text)


class Identity(TimestampMixin, Base):
    """Canonical merged person; bridge to the platform account via user_id (1:1)."""

    __tablename__ = "identity"
    __table_args__ = (
        sa.Index("uq_identity_email_lower", sa.text("lower(email)"), unique=True),
        sa.Index(
            "uq_identity_user_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)  # merge key, lower(email)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    user_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")
    )  # NULL → no platform login


class SourcePrincipal(TimestampMixin, Base):
    """Person as the source presents them; identity_id NULL → unmatched, awaits Admin."""

    __tablename__ = "source_principal"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "source_user_id", name="uq_source_principal_native"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str | None] = mapped_column(sa.Text)  # absent in some sources
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    identity_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("identity.id", ondelete="SET NULL"), index=True
    )
    # Admin's manual link (Identity Mapping tab): auto-match never overwrites it.
    pinned: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )


class SourceGroup(TimestampMixin, Base):
    """Source container: project · space · channel."""

    __tablename__ = "source_group"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "source_group_id", name="uq_source_group_native"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_group_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(sa.Text)  # from connector manifest


class GroupMembership(Base):
    """Snapshot of current membership (flat in v1); reconciliation adds/removes rows."""

    __tablename__ = "group_membership"

    source_group_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("source_group.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 2nd PK column is not covered by the PK prefix → own index for principal→groups resolve.
    source_principal_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("source_principal.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class Entity(TimestampMixin, Base):
    """Relational body — the source of truth; snapshot, not log (upsert overwrites)."""

    __tablename__ = "entities"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_id", "source_type", "source_entity_id", name="uq_entities_native"
        ),
        sa.CheckConstraint(f"status IN ({enum_check(EntityStatus)})", name="ck_entities_status"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)  # ticket · page · message
    source_entity_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text)
    body: Mapped[str | None] = mapped_column(sa.Text)  # sliced into chunks
    url: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str | None] = mapped_column(sa.Text)  # NULL allowed pre-classify
    author_principal_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("source_principal.id", ondelete="SET NULL"), index=True
    )
    source_created_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), index=True
    )  # staleness input
    is_deleted: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    deleted_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )  # set by Harvester reconciliation only
    content_hash: Mapped[str | None] = mapped_column(sa.Text)
    trust_score: Mapped[float | None] = mapped_column(sa.REAL)  # written by Curation Pass, stage 5
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Chunk(TimestampMixin, Base):
    """Text projection; is_deleted mirrors the parent — predicate of the partial indexes.

    embedding is halfvec(EMBEDDING_DIM) — the dimension is fixed once at the
    schema level; the assignment guard (ai_foundation registry) rejects models
    of another dimension until a dimension change becomes a schema operation
    (lifecycle.html#embedding-refresh, v2). NULL embedding = not embedded yet;
    searches skip such rows, the write path fills them best-effort.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        sa.UniqueConstraint("entity_id", "ordinal", name="uq_chunks_entity_ordinal"),
        sa.Index(
            "ix_chunks_text_tsv",
            "text_tsv",
            postgresql_using="gin",
            postgresql_where=sa.text("NOT is_deleted"),
        ),
        sa.Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
            postgresql_where=sa.text("NOT is_deleted"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    ordinal: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    text_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        sa.Computed(f"to_tsvector('{FTS_CONFIG}', text)", persisted=True),
    )
    token_count: Mapped[int | None] = mapped_column(sa.Integer)
    content_hash: Mapped[str | None] = mapped_column(sa.Text)  # re-embed only on text change
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(
        sa.Text, index=True
    )  # who embedded it — the refresh key (lifecycle.html#embedding-refresh)


class EntityEdge(TimestampMixin, Base):
    """Graph projection; the node IS the entities row. Traversal = recursive CTE."""

    __tablename__ = "entity_edge"
    __table_args__ = (
        sa.UniqueConstraint(
            "src_entity_id", "dst_entity_id", "rel_type", name="uq_entity_edge_triple"
        ),
        sa.CheckConstraint(f"rel_type IN ({enum_check(RelType)})", name="ck_entity_edge_rel_type"),
        sa.CheckConstraint(f"origin IN ({enum_check(EdgeOrigin)})", name="ck_entity_edge_origin"),
        # The recursive CTE takes the next hop by (src, rel_type).
        sa.Index("ix_entity_edge_src_rel", "src_entity_id", "rel_type"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    src_entity_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    dst_entity_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rel_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    weight: Mapped[float | None] = mapped_column(sa.REAL)  # edited by Curation Pass
    origin: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EntityRef(Base):
    """Unresolved-link staging: written at capture when the target node isn't in DB yet.

    Curation Pass (stage 5) resolves refs into entity_edge rows and deletes them —
    rows are immutable after insert (ON CONFLICT DO NOTHING), hence created_at only.
    """

    __tablename__ = "entity_ref"
    __table_args__ = (
        sa.UniqueConstraint(
            "src_entity_id", "relation", "target_kind", "target_ref", name="uq_entity_ref_natural"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    src_entity_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(sa.Text, nullable=False)  # source terms, not rel_type
    target_kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # issue · page · user · …
    target_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)  # native id, resolve key
    source_hint: Mapped[str | None] = mapped_column(sa.Text)  # jira · gitlab · NULL
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class EntityAcl(Base):
    """Grant on an entity — three forms: group / principal / public. Snapshot, no payload.

    The partial UNIQUEs double as resolve-direction lookups and ON CONFLICT targets.
    """

    __tablename__ = "entity_acl"
    __table_args__ = (
        sa.CheckConstraint(f"scope IN ({enum_check(AclScope)})", name="ck_entity_acl_scope"),
        sa.CheckConstraint(
            "(scope = 'group' AND source_group_id IS NOT NULL AND source_principal_id IS NULL)"
            " OR (scope = 'principal' AND source_principal_id IS NOT NULL"
            " AND source_group_id IS NULL)"
            " OR (scope = 'public' AND source_group_id IS NULL AND source_principal_id IS NULL)",
            name="ck_entity_acl_scope_fields",
        ),
        sa.Index(
            "uq_entity_acl_group",
            "source_group_id",
            "entity_id",
            unique=True,
            postgresql_where=sa.text("source_group_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_entity_acl_principal",
            "source_principal_id",
            "entity_id",
            unique=True,
            postgresql_where=sa.text("source_principal_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_entity_acl_public",
            "entity_id",
            unique=True,
            postgresql_where=sa.text("scope = 'public'"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_group_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("source_group.id", ondelete="CASCADE")
    )
    source_principal_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("source_principal.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class CurationRun(Base):
    """Platform-level run journal, mirror of sync_runs.

    Progress lives in the domain timestamps (started/finished/heartbeat), so no
    updated_at (run-journal convention).
    """

    __tablename__ = "curation_runs"
    __table_args__ = (
        sa.CheckConstraint(
            f'"trigger" IN ({enum_check(CurationTrigger)})', name="ck_curation_runs_trigger"
        ),
        sa.CheckConstraint(
            f"state IN ({enum_check(CurationState)})", name="ck_curation_runs_state"
        ),
        # Max one unfinished platform run; the reaper frees a zombie lock.
        sa.Index(
            "uq_curation_runs_active",
            sa.text("(true)"),
            unique=True,
            postgresql_where=sa.text("state IN ('queued','running')"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    trigger: Mapped[str] = mapped_column(sa.Text, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{CurationState.QUEUED}'")
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    steps: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # which steps ran + stats
    # Open destructive window (merge/retention): while set on a live running
    # run, sync mark_running yields — lane coordination (lifecycle.html#coordination).
    destructive_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class PlatformSettings(TimestampMixin, Base):
    """Singleton (id=1, seeded by the migration); the app reads/updates, never inserts.

    The org-wide configuration row (admin-panel/_workzone/data-model.html):
    branding/locale defaults, session TTLs, maintenance/integration switches,
    AI budget thresholds, plus the scheduling knobs Harvester and the KS ticks
    read (a source overrides via its own sync_interval/reconcile_* columns,
    NULL = inherit). Owner edits it on the Admin "Platform" screen; Admin reads.
    """

    __tablename__ = "platform_settings"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="ck_platform_settings_singleton"),
        sa.CheckConstraint(
            f"accent_color ~ '{ACCENT_COLOR_PATTERN}'", name="ck_platform_settings_accent_color"
        ),
        sa.CheckConstraint(
            f"locale IN ({enum_check(PlatformLocale)})", name="ck_platform_settings_locale"
        ),
        sa.CheckConstraint(
            f"date_format IN ({enum_check(DateFormat)})", name="ck_platform_settings_date_format"
        ),
        sa.CheckConstraint(
            f"access_token_ttl BETWEEN 1 AND {int(ACCESS_TOKEN_TTL_MAX.total_seconds())}",
            name="ck_platform_settings_access_ttl",
        ),
        sa.CheckConstraint("refresh_token_ttl > 0", name="ck_platform_settings_refresh_ttl"),
        sa.CheckConstraint("session_absolute_ttl > 0", name="ck_platform_settings_absolute_ttl"),
        # A session nests: access token ⊆ refresh window ⊆ absolute ceiling.
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
            f"curation_frequency IN ({enum_check(CadenceFrequency)})",
            name="ck_platform_settings_curation_frequency",
        ),
        sa.CheckConstraint(
            "curation_weekday IS NULL OR curation_weekday BETWEEN 0 AND 6",
            name="ck_platform_settings_curation_weekday",
        ),
        sa.CheckConstraint(
            f"curation_time ~ '{WINDOW_TIME_PATTERN}'",
            name="ck_platform_settings_curation_time",
        ),
        sa.CheckConstraint(
            "agent_weekly_token_budget IS NULL OR agent_weekly_token_budget > 0",
            name="ck_platform_settings_agent_budget",
        ),
        sa.CheckConstraint(
            "agent_iteration_cap > 0", name="ck_platform_settings_agent_iteration_cap"
        ),
        sa.CheckConstraint(
            "agent_max_concurrency > 0", name="ck_platform_settings_agent_concurrency"
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    # Organization & defaults (branding + locale resolution chain: personal
    # override -> these org defaults -> browser).
    org_name: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'Achilles'")
    )
    org_logo_url: Mapped[str | None] = mapped_column(sa.Text)
    org_description: Mapped[str | None] = mapped_column(sa.Text)
    accent_color: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'#6366f1'")
    )
    timezone: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'UTC'")
    )  # IANA name; org display + schedule windows
    locale: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'ru'"))
    date_format: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'DD.MM.YYYY'")
    )
    # Session TTLs in seconds — override the auth constants' defaults.
    access_token_ttl: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("900")
    )
    refresh_token_ttl: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("2592000")
    )
    session_absolute_ttl: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("7776000")
    )
    # Integrations & maintenance. maintenance_mode is mirrored into redis on
    # PATCH so the request path checks a flag, not the DB.
    maintenance_mode: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    mcp_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    # AI budget thresholds (ai-foundation/cost-accounting.html); the ledgers
    # live in model_usage / run journals. NULL = not set.
    ai_monthly_budget: Mapped[Decimal | None] = mapped_column(sa.Numeric)
    ai_budget_alert_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    chat_weekly_token_budget: Mapped[int | None] = mapped_column(sa.BigInteger)
    sync_interval_minutes: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("15")
    )
    # Minute-of-week in org time, Monday 00:00 = 0; default Sunday 03:00.
    reconcile_minute_of_week: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("8820")
    )
    watchdog_silence_hours: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("12")
    )
    # Curation Pass window — org-local, same cadence shape as backup_settings.
    curation_frequency: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{CadenceFrequency.DAILY}'")
    )
    curation_weekday: Mapped[int | None] = mapped_column(sa.Integer)
    curation_time: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'04:00'")
    )
    # Agent Engine run knobs (governance.html#budget, execution.html#concurrency).
    # NULL budget = no ceiling; concurrency is enforced by a DB gate at run
    # start, so a PATCH applies live without a worker restart.
    agent_weekly_token_budget: Mapped[int | None] = mapped_column(sa.BigInteger)
    agent_iteration_cap: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("15")
    )
    agent_max_concurrency: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("4")
    )


class BackupSettings(TimestampMixin, Base):
    """Singleton (id=1, seeded by the migration); the app reads/updates, never inserts."""

    __tablename__ = "backup_settings"
    __table_args__ = (
        sa.CheckConstraint("id = 1", name="ck_backup_settings_singleton"),
        sa.CheckConstraint(
            f"frequency IN ({enum_check(CadenceFrequency)})", name="ck_backup_settings_frequency"
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_backup_settings_weekday"),
        sa.CheckConstraint("retention_count > 0", name="ck_backup_settings_retention"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    destination_url: Mapped[str | None] = mapped_column(sa.Text)  # NULL → not configured
    # Write-only, encrypted by the crypto core; never returned in UI/export.
    # NULL → ambient IAM role.
    destination_creds_enc: Mapped[str | None] = mapped_column(sa.Text)
    frequency: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{CadenceFrequency.DAILY}'")
    )
    weekday: Mapped[int | None] = mapped_column(sa.Integer)  # weekly only; NULL for daily
    time: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'02:00'")
    )  # 'HH:MM' local
    retention_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("14")
    )


class BackupSnapshot(Base):
    """Append-only snapshot journal; single-active lock like curation_runs."""

    __tablename__ = "backup_snapshots"
    __table_args__ = (
        sa.CheckConstraint(
            f"state IN ({enum_check(BackupState)})", name="ck_backup_snapshots_state"
        ),
        sa.Index(
            "uq_backup_snapshots_active",
            sa.text("(true)"),
            unique=True,
            postgresql_where=sa.text("state = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{BackupState.RUNNING}'")
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )  # journal sort
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    location: Mapped[str | None] = mapped_column(sa.Text)  # storage path, restore pointer
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
