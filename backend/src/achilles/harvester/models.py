"""Harvester control layer — harvester/_workzone/data-model.html.

`sources` and `platform_settings` stay in knowledge_store/models.py (their
readers span modules and KS is the base of the import direction); Harvester
owns the writes. Here: the per-source run journal and the dead-letter work
queue.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from achilles.db.base import Base, TimestampMixin, enum_check
from achilles.harvester.constants import DlqReason, SyncMode, SyncState, SyncTrigger
from achilles.infra.lifecycle import uniqueness_lock_index


class SyncRun(Base):
    """Per-source run journal, mirror of curation_runs.

    Progress lives in the domain timestamps (started/finished/heartbeat), so no
    updated_at (run-journal convention). mode · trigger · scope are three
    independent axes; the history "type" of a row is derived, never stored.
    """

    __tablename__ = "sync_runs"
    __table_args__ = (
        sa.CheckConstraint(f"mode IN ({enum_check(SyncMode)})", name="ck_sync_runs_mode"),
        sa.CheckConstraint(
            f'"trigger" IN ({enum_check(SyncTrigger)})', name="ck_sync_runs_trigger"
        ),
        sa.CheckConstraint(f"state IN ({enum_check(SyncState)})", name="ck_sync_runs_state"),
        # One active run per source; the reaper frees a zombie lock.
        uniqueness_lock_index("sync_runs", "source_id"),
        # Run history is read per source, newest first.
        sa.Index("ix_sync_runs_source_created", "source_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(sa.Text, nullable=False)
    trigger: Mapped[str] = mapped_column(sa.Text, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text(f"'{SyncState.QUEUED}'")
    )
    # NULL = the whole source; a window (partial re-sync) or an item list (DLQ retry).
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    entities_done: Mapped[int | None] = mapped_column(sa.Integer)
    entities_total: Mapped[int | None] = mapped_column(sa.Integer)  # estimate for "N of M"
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # resume position
    error_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )  # items in the DLQ from this run
    error_detail: Mapped[str | None] = mapped_column(sa.Text)  # why the run failed
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class DeadLetter(TimestampMixin, Base):
    """Work queue of failed items, not a journal: resolved rows are deleted.

    One row per item (natural UNIQUE); a repeat failure updates attempts/reason
    instead of stacking rows.
    """

    __tablename__ = "dead_letters"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_id", "source_type", "source_entity_id", name="uq_dead_letters_item"
        ),
        sa.CheckConstraint(f"reason IN ({enum_check(DlqReason)})", name="ck_dead_letters_reason"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey("sync_runs.id", ondelete="SET NULL"), index=True
    )  # last run that touched the item
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_entity_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(sa.Text)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
