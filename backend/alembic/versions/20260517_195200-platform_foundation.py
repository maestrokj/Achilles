"""Platform foundation: pgvector extension + shared set_updated_at() trigger function.

Revision ID: 20260517_195200
Revises:
Create Date: 2026-05-17

The root of the chain owns only cross-cutting database plumbing; domain tables
arrive with their own stages (auth — stage 1, Knowledge Store — stage 2,
Harvester — stage 5) as separate migrations.
"""

from alembic import op

revision = "20260517_195200"
down_revision = None
branch_labels = None
depends_on = None

# Reused by every table with updated_at: the trigger bumps it on UPDATE,
# so the value is a DB-level fact, not an ORM courtesy.
UPDATED_AT_TRIGGER = """
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(UPDATED_AT_TRIGGER)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    op.execute("DROP EXTENSION IF EXISTS vector;")
