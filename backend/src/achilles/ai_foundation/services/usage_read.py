"""Read side of cost accounting: the four slices of the "AI spend" screen.

cost-accounting.html — one accounting contour, four projections:
company totals and the per-model money slice come from the model_usage
aggregate (catches the person-less spend: indexing, RAG search); the
per-person slice sums the journals (messages / agent_runs) in tokens.
The two overlap on chat and agents but the aggregate is wider — the company
total is deliberately ≠ the sum over people. Read-only: record_usage
(usage.py) stays the single writer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.models import Agent, AgentRun
from achilles.agent_engine.service import weekly_window_start
from achilles.ai_foundation.models import AgentModel, AiModel, AiProvider, ModelUsage
from achilles.api.pagination import OffsetParams, offset_window
from achilles.auth.models import User
from achilles.auth.services.users_admin import user_search_clause
from achilles.query_engine.constants import MessageRole
from achilles.query_engine.models import Conversation, Message

type UsageWindow = Literal["week", "prev_week", "month"]

BY_MODEL_DEFAULT_DAYS = 30
# Ceiling on the by-model lookback: without it an oversized ?model_days overflows
# the timedelta() below into an unhandled 500. Ten years dwarfs any real window.
BY_MODEL_MAX_DAYS = 366 * 10


@dataclass(frozen=True, slots=True)
class WindowTotal:
    tokens: int
    cost: Decimal | None  # None = at least one bucket lacks a price — not fully priced


@dataclass(frozen=True, slots=True)
class UserSpendRow:
    user: User
    agent_tokens: int
    chat_tokens: int


@dataclass(frozen=True, slots=True)
class ModelSpendRow:
    display_name: str | None  # None → the catalog row is gone, history survives
    provider_name: str | None
    function: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cost: Decimal | None


def window_bounds(
    window: UsageWindow, *, now: datetime, org_tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Per-person window bounds.

    Calendar week (Sunday-reset, shared with the agents budget), the previous
    one, or the current calendar month.
    """
    week_start = weekly_window_start(now, org_tz)
    if window == "week":
        return week_start, now
    if window == "prev_week":
        return week_start - timedelta(days=7), week_start
    return month_start(now, org_tz).astimezone(UTC), now


def month_start(now: datetime, org_tz: ZoneInfo) -> datetime:
    """The current calendar month's first local moment — the month-window anchor."""
    local = now.astimezone(org_tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def usage_total(session: AsyncSession, *, since_local_date: datetime) -> WindowTotal:
    """One window over model_usage — tokens + cost (None while any bucket is unpriced)."""
    tokens, cost, unpriced = (
        await session.execute(
            sa.select(*_window_aggregates()).where(
                ModelUsage.bucket_date >= since_local_date.date()
            )
        )
    ).one()
    return WindowTotal(tokens=int(tokens), cost=None if unpriced else cost)


async def monthly_spend(session: AsyncSession, *, since_local_date: datetime) -> Decimal:
    """Priced spend since the anchor — the budget-alert predicate (unpriced buckets add 0)."""
    spent = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(ModelUsage.cost), 0)).where(
            ModelUsage.bucket_date >= since_local_date.date()
        )
    )
    return spent or Decimal(0)  # the coalesce pins the row; the `or` pins the type


def _window_aggregates(
    since: sa.ColumnElement[bool] | None = None,
) -> tuple[sa.ColumnElement[Any], ...]:
    """(tokens, cost, unpriced) aggregates, optionally FILTERed to a sub-window."""
    conds = () if since is None else (since,)
    tokens = sa.func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens).filter(*conds)
    cost = sa.func.sum(ModelUsage.cost).filter(*conds)
    unpriced = sa.func.count().filter(ModelUsage.cost.is_(None), *conds)
    return sa.func.coalesce(tokens, 0), cost, unpriced


async def company_totals(
    session: AsyncSession, *, now: datetime, org_tz: ZoneInfo
) -> dict[str, WindowTotal]:
    """The panorama tiles: calendar week - month - year, all functions.

    Read from model_usage (a derived SUM, not a counter table). week ⊂ month ⊂
    year, so one scan over the year window with FILTER aggregates covers all three.
    """
    local = now.astimezone(org_tz)
    week = weekly_window_start(now, org_tz).astimezone(org_tz).date()
    month = month_start(now, org_tz).date()
    year = local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).date()
    row = (
        await session.execute(
            sa.select(
                *_window_aggregates(ModelUsage.bucket_date >= week),
                *_window_aggregates(ModelUsage.bucket_date >= month),
                *_window_aggregates(),
            ).where(ModelUsage.bucket_date >= year)
        )
    ).one()
    return {
        name: WindowTotal(tokens=int(tokens), cost=None if unpriced else cost)
        for name, (tokens, cost, unpriced) in zip(
            ("week", "month", "year"), (row[0:3], row[3:6], row[6:9]), strict=True
        )
    }


def _agent_spend_subq(start: datetime, end: datetime) -> sa.Subquery:
    return (
        sa.select(
            Agent.user_id.label("user_id"),
            sa.func.sum(AgentRun.tokens_used).label("tokens"),
        )
        .join(AgentRun, AgentRun.agent_id == Agent.id)
        .where(AgentRun.finished_at >= start, AgentRun.finished_at < end)
        .group_by(Agent.user_id)
        .subquery()
    )


def _chat_spend_subq(start: datetime, end: datetime) -> sa.Subquery:
    return (
        sa.select(
            Conversation.user_id.label("user_id"),
            sa.func.sum(Message.tokens_used).label("tokens"),
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .where(
            Message.role == str(MessageRole.ASSISTANT),
            Message.created_at >= start,
            Message.created_at < end,
        )
        .group_by(Conversation.user_id)
        .subquery()
    )


async def per_user(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    q: str | None,
    roles: list[str] | None,
    params: OffsetParams,
) -> tuple[list[UserSpendRow], int, int]:
    """The people slice: agents + chat tokens per user, heaviest first."""
    agents = _agent_spend_subq(start, end)
    chat = _chat_spend_subq(start, end)
    agent_tokens = sa.func.coalesce(agents.c.tokens, 0)
    chat_tokens = sa.func.coalesce(chat.c.tokens, 0)

    filters: list[sa.ColumnElement[bool]] = []
    if q:
        filters.append(user_search_clause(q.strip()))
    if roles:
        filters.append(User.role.in_(roles))

    stmt = (
        sa.select(User, agent_tokens, chat_tokens)
        .outerjoin(agents, agents.c.user_id == User.id)
        .outerjoin(chat, chat.c.user_id == User.id)
        .where(*filters)
        .order_by((agent_tokens + chat_tokens).desc(), User.id)
    )
    # The LEFT-joined aggregates never multiply rows — count plain users instead
    # of re-running both GROUP-BY subqueries inside the count.
    count_stmt = sa.select(sa.func.count()).select_from(User).where(*filters)
    total, page = await offset_window(session, stmt, params, count_stmt=count_stmt)
    rows = await session.execute(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return (
        [
            UserSpendRow(user=user, agent_tokens=int(agent_sum), chat_tokens=int(chat_sum))
            for user, agent_sum, chat_sum in rows.tuples()
        ],
        total,
        page,
    )


async def user_agents_breakdown(
    session: AsyncSession, *, user_id: int, start: datetime, end: datetime
) -> list[tuple[int, str, str | None, int, int]]:
    """Per agent, not per model (the run journal keeps no model).

    The "Model" column is the agent's current model, per the design's honest caveat.
    """
    rows = await session.execute(
        sa.select(
            Agent.id,
            Agent.name,
            AiModel.display_name,
            sa.func.count(AgentRun.id),
            sa.func.coalesce(sa.func.sum(AgentRun.tokens_used), 0),
        )
        .join(AgentRun, AgentRun.agent_id == Agent.id)
        .outerjoin(AgentModel, AgentModel.id == Agent.model_id)
        .outerjoin(AiModel, AiModel.id == AgentModel.model_id)
        .where(
            Agent.user_id == user_id,
            AgentRun.finished_at >= start,
            AgentRun.finished_at < end,
        )
        .group_by(Agent.id, Agent.name, AiModel.display_name)
        .order_by(sa.func.coalesce(sa.func.sum(AgentRun.tokens_used), 0).desc())
    )
    return [
        (int(agent_id), name, model, int(runs), int(tokens))
        for agent_id, name, model, runs, tokens in rows.tuples()
    ]


async def user_chat_breakdown(
    session: AsyncSession, *, user_id: int, start: datetime, end: datetime
) -> list[tuple[str, int, int]]:
    """Per model — exact: every assistant message carries model + tokens."""
    rows = await session.execute(
        sa.select(
            sa.func.coalesce(Message.model, "—"),
            sa.func.count(Message.id),
            sa.func.coalesce(sa.func.sum(Message.tokens_used), 0),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.user_id == user_id,
            Message.role == str(MessageRole.ASSISTANT),
            Message.created_at >= start,
            Message.created_at < end,
        )
        .group_by(Message.model)
        .order_by(sa.func.coalesce(sa.func.sum(Message.tokens_used), 0).desc())
    )
    return [(model, int(messages), int(tokens or 0)) for model, messages, tokens in rows.tuples()]


async def by_model(
    session: AsyncSession, *, now: datetime, org_tz: ZoneInfo, days: int
) -> list[ModelSpendRow]:
    """The money slice per model · function; unpriced rows sink to the tail."""
    floor = (now.astimezone(org_tz) - timedelta(days=days)).date()
    rows = await session.execute(
        sa.select(
            AiModel.display_name,
            AiProvider.name,
            ModelUsage.function,
            sa.func.coalesce(sa.func.sum(ModelUsage.request_count), 0),
            sa.func.coalesce(sa.func.sum(ModelUsage.input_tokens), 0),
            sa.func.coalesce(sa.func.sum(ModelUsage.output_tokens), 0),
            sa.func.sum(ModelUsage.cost),
            sa.func.count().filter(ModelUsage.cost.is_(None)),
        )
        .outerjoin(AiModel, AiModel.id == ModelUsage.model_id)
        .outerjoin(AiProvider, AiProvider.id == AiModel.provider_id)
        .where(ModelUsage.bucket_date >= floor)
        .group_by(AiModel.display_name, AiProvider.name, ModelUsage.function)
        .order_by(sa.func.sum(ModelUsage.cost).desc().nulls_last())
    )
    return [
        ModelSpendRow(
            display_name=display_name,
            provider_name=provider_name,
            function=function,
            request_count=int(requests),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost=None if unpriced else cost,
        )
        for (
            display_name,
            provider_name,
            function,
            requests,
            input_tokens,
            output_tokens,
            cost,
            unpriced,
        ) in rows.tuples()
    ]
