"""Knowledge Store schema: entities + chunks + entity_edge, ACL five, run journals, backups.

Revision ID: 20260702_140000
Revises: 20260702_120000
Create Date: 2026-07-02

Design: docs/architecture/modules/knowledge-store/_workzone/
(data-model, acl-identity, lifecycle).
`sources` is a minimal stub — Harvester (stage 5) extends it with its own migration.
Vector columns on chunks (embedding halfvec(N) + HNSW) arrive in stage 4, when an
embedding model is assigned and N is known. An FK whose composite UNIQUE/index
leads with it carries no extra single-column index (pure write amplification).
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260702_140000"
down_revision = "20260702_120000"
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


def _source_fk() -> sa.Column[int]:
    return sa.Column(
        "source_id",
        sa.BigInteger,
        sa.ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )


def _entity_fk(name: str = "entity_id", *, index: bool = False) -> sa.Column[int]:
    return sa.Column(
        name,
        sa.BigInteger,
        sa.ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=index,
    )


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("connector_type", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("authority_tier", sa.Text),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("state IN ('active','paused','disconnected')", name="ck_sources_state"),
        sa.CheckConstraint(
            "authority_tier IN ('low','normal','high')", name="ck_sources_authority_tier"
        ),
    )
    _add_updated_at_trigger("sources")

    op.create_table(
        "identity",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _created_at(),
        _updated_at(),
    )
    op.create_index("uq_identity_email_lower", "identity", [sa.text("lower(email)")], unique=True)
    op.create_index(
        "uq_identity_user_id",
        "identity",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    _add_updated_at_trigger("identity")

    op.create_table(
        "source_principal",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _source_fk(),
        sa.Column("source_user_id", sa.Text, nullable=False),
        sa.Column("email", sa.Text),
        sa.Column("display_name", sa.Text),
        sa.Column(
            "identity_id",
            sa.BigInteger,
            sa.ForeignKey("identity.id", ondelete="SET NULL"),
            index=True,
        ),
        # Admin's manual link (Identity Mapping tab): auto-match never overwrites it.
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("source_id", "source_user_id", name="uq_source_principal_native"),
    )
    _add_updated_at_trigger("source_principal")

    op.create_table(
        "source_group",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _source_fk(),
        sa.Column("source_group_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("source_id", "source_group_id", name="uq_source_group_native"),
    )
    _add_updated_at_trigger("source_group")

    op.create_table(
        "group_membership",
        sa.Column(
            "source_group_id",
            sa.BigInteger,
            sa.ForeignKey("source_group.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_principal_id",
            sa.BigInteger,
            sa.ForeignKey("source_principal.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        _created_at(),
    )

    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _source_fk(),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_entity_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("body", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column(
            "author_principal_id",
            sa.BigInteger,
            sa.ForeignKey("source_principal.id", ondelete="SET NULL"),
            index=True,
        ),
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), index=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.Text),
        sa.Column("trust_score", sa.REAL),
        sa.Column("meta", postgresql.JSONB),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint(
            "source_id", "source_type", "source_entity_id", name="uq_entities_native"
        ),
        sa.CheckConstraint("status IN ('draft','final','archived')", name="ck_entities_status"),
    )
    _add_updated_at_trigger("entities")

    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _entity_fk(),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "text_tsv",
            postgresql.TSVECTOR,
            sa.Computed("to_tsvector('simple', text)", persisted=True),
        ),
        sa.Column("token_count", sa.Integer),
        sa.Column("content_hash", sa.Text),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("entity_id", "ordinal", name="uq_chunks_entity_ordinal"),
    )
    op.create_index(
        "ix_chunks_text_tsv",
        "chunks",
        ["text_tsv"],
        postgresql_using="gin",
        postgresql_where=sa.text("NOT is_deleted"),
    )
    _add_updated_at_trigger("chunks")

    op.create_table(
        "entity_edge",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _entity_fk("src_entity_id"),
        _entity_fk("dst_entity_id", index=True),
        sa.Column("rel_type", sa.Text, nullable=False),
        sa.Column("weight", sa.REAL),
        sa.Column("origin", sa.Text, nullable=False),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint(
            "src_entity_id", "dst_entity_id", "rel_type", name="uq_entity_edge_triple"
        ),
        sa.CheckConstraint(
            "rel_type IN ('mentions','replies_to','links_to','child_of','duplicate_of')",
            name="ck_entity_edge_rel_type",
        ),
        sa.CheckConstraint("origin IN ('harvester','curation')", name="ck_entity_edge_origin"),
    )
    op.create_index("ix_entity_edge_src_rel", "entity_edge", ["src_entity_id", "rel_type"])
    _add_updated_at_trigger("entity_edge")

    op.create_table(
        "entity_ref",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _entity_fk("src_entity_id"),
        sa.Column("relation", sa.Text, nullable=False),
        sa.Column("target_kind", sa.Text, nullable=False),
        sa.Column("target_ref", sa.Text, nullable=False),
        sa.Column("source_hint", sa.Text),
        _created_at(),
        sa.UniqueConstraint(
            "src_entity_id", "relation", "target_kind", "target_ref", name="uq_entity_ref_natural"
        ),
    )

    op.create_table(
        "entity_acl",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _entity_fk(index=True),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column(
            "source_group_id",
            sa.BigInteger,
            sa.ForeignKey("source_group.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "source_principal_id",
            sa.BigInteger,
            sa.ForeignKey("source_principal.id", ondelete="CASCADE"),
        ),
        _created_at(),
        sa.CheckConstraint("scope IN ('group','principal','public')", name="ck_entity_acl_scope"),
        sa.CheckConstraint(
            "(scope = 'group' AND source_group_id IS NOT NULL AND source_principal_id IS NULL)"
            " OR (scope = 'principal' AND source_principal_id IS NOT NULL"
            " AND source_group_id IS NULL)"
            " OR (scope = 'public' AND source_group_id IS NULL AND source_principal_id IS NULL)",
            name="ck_entity_acl_scope_fields",
        ),
    )
    op.create_index(
        "uq_entity_acl_group",
        "entity_acl",
        ["source_group_id", "entity_id"],
        unique=True,
        postgresql_where=sa.text("source_group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_entity_acl_principal",
        "entity_acl",
        ["source_principal_id", "entity_id"],
        unique=True,
        postgresql_where=sa.text("source_principal_id IS NOT NULL"),
    )
    op.create_index(
        "uq_entity_acl_public",
        "entity_acl",
        ["entity_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'public'"),
    )

    op.create_table(
        "curation_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("started_at", sa.DateTime(timezone=True), index=True),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("steps", postgresql.JSONB),
        sa.Column("error", sa.Text),
        _created_at(),
        sa.CheckConstraint(
            "\"trigger\" IN ('schedule','model_change','manual')", name="ck_curation_runs_trigger"
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_curation_runs_state",
        ),
    )
    op.create_index(
        "uq_curation_runs_active",
        "curation_runs",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("state IN ('queued','running')"),
    )

    op.create_table(
        "backup_settings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("destination_url", sa.Text),
        sa.Column("destination_creds_enc", sa.Text),
        sa.Column("frequency", sa.Text, nullable=False, server_default=sa.text("'daily'")),
        sa.Column("weekday", sa.Integer),
        sa.Column("time", sa.Text, nullable=False, server_default=sa.text("'02:00'")),
        sa.Column("retention_count", sa.Integer, nullable=False, server_default=sa.text("14")),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("id = 1", name="ck_backup_settings_singleton"),
        sa.CheckConstraint("frequency IN ('daily','weekly')", name="ck_backup_settings_frequency"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_backup_settings_weekday"),
        sa.CheckConstraint("retention_count > 0", name="ck_backup_settings_retention"),
    )
    _add_updated_at_trigger("backup_settings")
    # Singleton seed — the application reads/updates this row, never inserts it.
    op.execute("INSERT INTO backup_settings (id) VALUES (1);")

    op.create_table(
        "backup_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'running'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("location", sa.Text),
        sa.Column("error", sa.Text),
        _created_at(),
        sa.CheckConstraint(
            "state IN ('running','succeeded','failed')", name="ck_backup_snapshots_state"
        ),
    )
    op.create_index(
        "uq_backup_snapshots_active",
        "backup_snapshots",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("state = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("backup_snapshots")
    _drop_updated_at_trigger("backup_settings")
    op.drop_table("backup_settings")
    op.drop_table("curation_runs")
    op.drop_table("entity_acl")
    op.drop_table("entity_ref")
    _drop_updated_at_trigger("entity_edge")
    op.drop_table("entity_edge")
    _drop_updated_at_trigger("chunks")
    op.drop_table("chunks")
    _drop_updated_at_trigger("entities")
    op.drop_table("entities")
    op.drop_table("group_membership")
    _drop_updated_at_trigger("source_group")
    op.drop_table("source_group")
    _drop_updated_at_trigger("source_principal")
    op.drop_table("source_principal")
    _drop_updated_at_trigger("identity")
    op.drop_table("identity")
    _drop_updated_at_trigger("sources")
    op.drop_table("sources")
