from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def enum_check(values: type[StrEnum]) -> str:
    """SQL list for a CHECK constraint: "'a','b','c'" — one source with the enum."""
    return ",".join(f"'{v.value}'" for v in values)


class TimestampMixin:
    """created_at / updated_at columns. updated_at is bumped by a DB trigger, not the ORM."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
