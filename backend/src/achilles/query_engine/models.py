"""Query Engine data model — query-engine/_workzone/data-model.html.

Conversation state is QE's own; the Knowledge Store is referenced by bare ids
(entity_ref, citations) — links, never copies (#boundary). messages is
append-only in content; only feedback mutates it. retrieval_trace is the
immutable snapshot of one grounded answer; access_counter is the demand
signal KS reads by JOIN at staleness time.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin, enum_check
from achilles.query_engine.constants import FEEDBACK_VALUES, FinishReason, MessageRole, Surface


class Conversation(TimestampMixin, Base):
    """One dialogue of one user on one surface; born lazily with the first message."""

    __tablename__ = "conversations"
    __table_args__ = (
        sa.CheckConstraint(f"surface IN ({enum_check(Surface)})", name="ck_conversations_surface"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    surface: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text)  # auto-generated from the first message
    # The sticky *intent*: validated against the chat_models allow-list on write,
    # NULL = the list's default. The *fact* of a generation is messages.model.
    selected_model: Mapped[str | None] = mapped_column(sa.Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # slack thread key etc., stage 8


class Message(TimestampMixin, Base):
    """Append-only content; feedback is the one mutable field (hence updated_at)."""

    __tablename__ = "messages"
    __table_args__ = (
        sa.CheckConstraint(f"role IN ({enum_check(MessageRole)})", name="ck_messages_role"),
        sa.CheckConstraint(
            f"feedback IN ({', '.join(str(v) for v in FEEDBACK_VALUES)})",
            name="ck_messages_feedback",
        ),
        sa.CheckConstraint(f"finish IN ({enum_check(FinishReason)})", name="ck_messages_finish"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model: Mapped[str | None] = mapped_column(sa.Text)  # generation fact, assistant only
    # The whole turn (input + output), stamped at finalization; NULL on a broken
    # stream — the raw material of per-person spend (cost-accounting.html).
    tokens_used: Mapped[int | None] = mapped_column(sa.BigInteger)
    feedback: Mapped[int | None] = mapped_column(sa.SmallInteger)  # 👍 1 · 👎 -1
    # Terminal outcome of an assistant turn, write-once at finalization; NULL =
    # completed cleanly. A failed turn always lands a row (even empty content)
    # so a reload can replay its notice instead of a silent dangling question.
    finish: Mapped[str | None] = mapped_column(sa.Text)
    error_code: Mapped[str | None] = mapped_column(sa.Text)  # the reason when finish='failed'


class RetrievalTrace(Base):
    """Immutable 0..1 snapshot of the search behind one assistant message."""

    __tablename__ = "retrieval_trace"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        sa.ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    search_query: Mapped[str] = mapped_column(sa.Text, nullable=False)  # the standalone query
    candidates: Mapped[list[Any] | None] = mapped_column(JSONB)  # KS ids + scores, links
    citations: Mapped[list[Any] | None] = mapped_column(JSONB)  # marker → entity/chunk + score
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class AccessCounter(TimestampMixin, Base):
    """Demand per KS entity: hits++ on every citation (upsert, UNIQUE entity_ref)."""

    __tablename__ = "access_counter"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    # A link across the module boundary, deliberately not an FK (#boundary);
    # merge transfer of the signal is a stage-5+ open question.
    entity_ref: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, unique=True)
    hits: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, server_default=sa.text("0"))
    last_accessed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
