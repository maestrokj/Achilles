"""User's personal sticky chat model — users.last_chat_model.

Revision ID: 20260713_120000
Revises: 20260712_120000
Create Date: 2026-07-13

Design: conversation.html#route — model stickiness graduates from per-conversation
to per-user. The last model a user explicitly picks on a selectable surface (Web)
becomes their personal default, seeding every *new* conversation ahead of the
admin default. NULL → the user has never picked; fall through to the admin default.
Surfaces without a picker (Slack/Telegram/MCP/agents) never write or read it.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260713_120000"
down_revision = "20260712_120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_chat_model", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_chat_model")
