"""Vector columns on chunks: embedding halfvec(1024) + embedding_model + partial HNSW.

Revision ID: 20260704_120000
Revises: 20260703_120000
Create Date: 2026-07-04

Design: knowledge-store/_workzone/data-model.html#chunks. The dimension is
fixed at EMBEDDING_DIM=1024 (both builtin models); the assignment guard in
ai_foundation rejects models of another dimension — a dimension change is a
schema operation (lifecycle.html#embedding-refresh, v2). The HNSW index is
partial on NOT is_deleted, mirroring the FTS GIN; the ACL JOIN stays the one
predicate hnsw.iterative_scan cannot compensate (decision box at #chunks).
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC

revision = "20260704_120000"
down_revision = "20260703_120000"
branch_labels = None
depends_on = None

_DIM = 1024  # ai_foundation.constants.EMBEDDING_DIM, frozen at migration time


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding", HALFVEC(_DIM)))
    op.add_column("chunks", sa.Column("embedding_model", sa.Text))
    op.create_index("ix_chunks_embedding_model", "chunks", ["embedding_model"])
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "halfvec_cosine_ops"},
        postgresql_where=sa.text("NOT is_deleted"),
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index("ix_chunks_embedding_model", table_name="chunks")
    op.drop_column("chunks", "embedding_model")
    op.drop_column("chunks", "embedding")
