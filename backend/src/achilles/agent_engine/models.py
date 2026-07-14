"""Agent Engine data model — agent-engine/_workzone/data-model.html.

The module stores agent definitions and their run journal; everything else
(models list, tools catalog, platform knobs) is read from the neighbours.
agent_runs follows the run-journal convention: created_at only, progress in
the domain timestamps, per-agent single-flight lock next to the journal.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from achilles.agent_engine.constants import AgentRunReason, AgentRunState, AgentRunTrigger
from achilles.db.base import Base, TimestampMixin, enum_check
from achilles.infra.lifecycle import uniqueness_lock_index


class Agent(TimestampMixin, Base):
    """Personal agent definition; the owner's ACL is its identity before KS.

    Two locks on independent axes: `enabled` is the owner's full stop
    (schedule and manual alike), `admin_paused` is the sticky admin lock the
    owner cannot lift. next_run_at is the scheduler's scan key — NULL for
    manual-only, disabled, locked or model-less agents (a durable stop leaves
    no journal noise).
    """

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)  # card subtitle, for humans
    prompt: Mapped[str] = mapped_column(sa.Text, nullable=False)  # owner layer of the composition
    # Discriminated union (schemas.ScheduleSpec); NULL = manual only.
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # References the allowed-list row, not the model catalog: removing a model
    # from the list is what stops agents (SET NULL → gate closes).
    model_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("agent_models.id", ondelete="SET NULL"), index=True
    )
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    admin_paused: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    next_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AgentRun(Base):
    """Per-agent run journal, mirror of sync_runs.

    Progress lives in the domain timestamps (started/finished/heartbeat), so
    no updated_at. `skipped` rows exist only when the engine took the start
    but a runtime gate closed; durable stops never reach the journal.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        sa.CheckConstraint(
            f'"trigger" IN ({enum_check(AgentRunTrigger)})', name="ck_agent_runs_trigger"
        ),
        sa.CheckConstraint(f"state IN ({enum_check(AgentRunState)})", name="ck_agent_runs_state"),
        sa.CheckConstraint(
            f"reason IN ({enum_check(AgentRunReason)})", name="ck_agent_runs_reason"
        ),
        # One active run per agent; the reaper frees a zombie lock.
        uniqueness_lock_index("agent_runs", "agent_id"),
        # Run history is read per agent, newest first.
        # Journal reads (history page, DISTINCT ON last-run) order by id —
        # creation order for an identity PK — so the index matches that key.
        sa.Index("ix_agent_runs_agent_id_id", "agent_id", "id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(sa.Text, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{AgentRunState.QUEUED}'")
    )
    reason: Mapped[str | None] = mapped_column(sa.Text)  # machine code for skipped/failed
    output: Mapped[str | None] = mapped_column(sa.Text)  # the agent's final answer
    tokens_used: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )  # summed into the owner's weekly budget
    error: Mapped[str | None] = mapped_column(sa.Text)  # failure detail
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # The weekly budget window filters on finished_at.
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class AgentTool(Base):
    """Optional tools on top of the locked KS core (which never hits the table).

    Append-only selection rows; losing one (agents_allowed=false or a catalog
    CASCADE) is soft degradation, not a start-gate condition.
    """

    __tablename__ = "agent_tools"
    __table_args__ = (sa.UniqueConstraint("agent_id", "tool_id", name="uq_agent_tools_pair"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    agent_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
