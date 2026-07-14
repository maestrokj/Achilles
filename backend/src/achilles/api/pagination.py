"""List contracts — admin-panel/_workzone/list-controls.html#pagination.

Two shapes, picked by surface: infinite feeds (chat, runs, agents) use keyset
``{items, next_cursor}`` with an opaque cursor; admin tables use offset
``{items, total, page, per_page}`` for the numbered "X-Y of N" control.
Ordering must be deterministic with an ``id`` tie-break in both.
"""

import base64
import binascii
import json
import math
from typing import Annotated, Any, Protocol, cast

import sqlalchemy as sa
from fastapi import Query
from pydantic import AfterValidator, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from achilles.api.problems import CODE_VALIDATION_ERROR, ApiError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

type CursorValue = int | str

LimitParam = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
CursorParam = Annotated[str | None, Query()]


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(values: list[CursorValue]) -> str:
    raw = json.dumps(values, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[CursorValue]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values: object = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error) as exc:
        raise _bad_cursor() from exc
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(v, int | str) for v in values)
    ):
        raise _bad_cursor()
    return cast("list[CursorValue]", values)


def _bad_cursor() -> ApiError:
    return ApiError(
        422,
        CODE_VALIDATION_ERROR,
        "Validation error",
        "Malformed pagination cursor",
        errors=[{"field": "cursor", "message": "malformed cursor"}],
    )


PER_PAGE_CHOICES = (10, 25, 50, 100)


def _per_page_choice(value: int) -> int:
    # Literal[int] would reject the query string before coercion — validate after.
    if value not in PER_PAGE_CHOICES:
        msg = "Input should be 10, 25, 50 or 100"
        raise ValueError(msg)
    return value


PageParam = Annotated[int, Query(ge=1)]
PerPageParam = Annotated[int, Query(), AfterValidator(_per_page_choice)]


class OffsetPage[T](BaseModel):
    items: list[T]
    total: int
    page: int
    per_page: int


class OffsetParams:
    """Query params for admin tables; page is clamped in offset_page."""

    def __init__(self, page: PageParam = 1, per_page: PerPageParam = 50) -> None:
        self.page = page
        self.per_page = per_page


class _HasId(Protocol):
    id: Any


async def keyset_page[RowT: _HasId](
    session: AsyncSession,
    stmt: sa.Select[tuple[RowT]],
    id_column: InstrumentedAttribute[int],
    *,
    limit: int,
    cursor: str | None,
    descending: bool = False,
) -> tuple[list[RowT], str | None]:
    """The one keyset-page mechanic: where-clause, over-fetch by 1, next cursor.

    ``stmt`` must already be ordered by ``id_column`` in the same direction as
    ``descending``; the id tie-break keeps the cursor stable across inserts.
    """
    after = decode_cursor(cursor)[0] if cursor else None
    if isinstance(after, int):
        stmt = stmt.where(id_column < after if descending else id_column > after)
    rows = list(await session.scalars(stmt.limit(limit + 1)))
    items = rows[:limit]
    next_cursor = encode_cursor([items[-1].id]) if len(rows) > limit and items else None
    return items, next_cursor


async def offset_window(
    session: AsyncSession,
    stmt: sa.Select[Any],
    params: OffsetParams,
    *,
    count_stmt: sa.Select[tuple[int]] | None = None,
) -> tuple[int, int]:
    """Count + clamp past-the-end to the last page; returns (total, effective page).

    The count is derived from ``stmt`` (count over its subquery); pass
    ``count_stmt`` only when a cheaper equivalent exists.
    """
    if count_stmt is None:
        count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
    total = (await session.scalar(count_stmt)) or 0
    last_page = max(1, math.ceil(total / params.per_page))
    return total, min(params.page, last_page)


async def offset_page[RowT](
    session: AsyncSession,
    stmt: sa.Select[tuple[RowT]],
    params: OffsetParams,
    *,
    count_stmt: sa.Select[tuple[int]] | None = None,
) -> tuple[list[RowT], int, int]:
    """The one offset-page mechanic: count, clamp, fetch the slice.

    ``stmt`` must already be deterministically ordered and select one entity;
    multi-entity selects use ``offset_window`` and fetch themselves. Returns
    ``(items, total, page)`` — ``page`` is the effective (clamped) one.
    """
    total, page = await offset_window(session, stmt, params, count_stmt=count_stmt)
    rows = await session.scalars(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return list(rows), total, page
