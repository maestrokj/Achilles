"""AI spend routes: the four slices + one person's breakdown (usage wireframes).

Limits are written via PATCH /admin/settings (Owner); this surface reads.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from achilles.agent_engine.service import budget_exceeded, org_zone
from achilles.ai_foundation.routes import AiAdmin
from achilles.ai_foundation.services import usage_read
from achilles.ai_foundation.services.usage_read import (
    BY_MODEL_DEFAULT_DAYS,
    BY_MODEL_MAX_DAYS,
    UsageWindow,
)
from achilles.api.pagination import OffsetPage, OffsetParams
from achilles.api.problems import CODE_NOT_FOUND, ApiError
from achilles.auth.constants import UserRole
from achilles.auth.models import User
from achilles.db.dependencies import DbSession
from achilles.knowledge_store.services import platform

router = APIRouter(prefix="/admin/usage", tags=["admin-ai"])


class WindowTotalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tokens: int
    cost: Decimal | None


class TotalsOut(BaseModel):
    week: WindowTotalOut
    month: WindowTotalOut
    year: WindowTotalOut


class LimitsOut(BaseModel):
    agent_weekly_token_budget: int | None
    chat_weekly_token_budget: int | None
    ai_monthly_budget: Decimal | None
    ai_budget_alert_enabled: bool


class UserSpendOut(BaseModel):
    user_id: int
    full_name: str
    email: str
    role: str
    agent_tokens: int
    chat_tokens: int
    total_tokens: int
    agent_over_limit: bool
    chat_over_limit: bool


class ModelSpendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    display_name: str | None
    provider_name: str | None
    function: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cost: Decimal | None


class UsageOut(BaseModel):
    totals: TotalsOut
    limits: LimitsOut
    by_user: OffsetPage[UserSpendOut]
    by_model: list[ModelSpendOut]


class AgentBreakdownOut(BaseModel):
    agent_id: int
    name: str
    model: str | None
    runs: int
    tokens: int


class ChatBreakdownOut(BaseModel):
    model: str
    messages: int
    tokens: int


class UserUsageOut(BaseModel):
    user_id: int
    full_name: str
    email: str
    agent_tokens: int
    chat_tokens: int
    limits: LimitsOut
    agents: list[AgentBreakdownOut]
    chat: list[ChatBreakdownOut]


def _limits_out(row: object) -> LimitsOut:
    return LimitsOut.model_validate(row, from_attributes=True)


@router.get("")
async def read_usage(
    user: AiAdmin,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    q: str | None = None,
    role: Annotated[list[UserRole] | None, Query()] = None,
    window: UsageWindow = "week",
    model_days: Annotated[int, Query(ge=1, le=BY_MODEL_MAX_DAYS)] = BY_MODEL_DEFAULT_DAYS,
) -> UsageOut:
    del user
    settings_row = await platform.get_platform_settings(session)
    tz = org_zone(settings_row)
    now = datetime.now(UTC)
    start, end = usage_read.window_bounds(window, now=now, org_tz=tz)

    totals = await usage_read.company_totals(session, now=now, org_tz=tz)
    rows, total, page = await usage_read.per_user(
        session,
        start=start,
        end=end,
        q=q,
        roles=[r.value for r in role] if role else None,
        params=params,
    )
    models = await usage_read.by_model(session, now=now, org_tz=tz, days=model_days)

    # The over-limit pills compare against the *current* week's ceilings; for
    # past windows they stay informative rather than judgemental.
    agent_budget = settings_row.agent_weekly_token_budget
    chat_budget = settings_row.chat_weekly_token_budget
    return UsageOut(
        totals=TotalsOut(
            week=WindowTotalOut.model_validate(totals["week"]),
            month=WindowTotalOut.model_validate(totals["month"]),
            year=WindowTotalOut.model_validate(totals["year"]),
        ),
        limits=_limits_out(settings_row),
        by_user=OffsetPage(
            items=[
                UserSpendOut(
                    user_id=row.user.id,
                    full_name=row.user.full_name,
                    email=row.user.email,
                    role=row.user.role,
                    agent_tokens=row.agent_tokens,
                    chat_tokens=row.chat_tokens,
                    total_tokens=row.agent_tokens + row.chat_tokens,
                    agent_over_limit=budget_exceeded(row.agent_tokens, agent_budget),
                    chat_over_limit=budget_exceeded(row.chat_tokens, chat_budget),
                )
                for row in rows
            ],
            total=total,
            page=page,
            per_page=params.per_page,
        ),
        by_model=[ModelSpendOut.model_validate(m) for m in models],
    )


@router.get("/{user_id}")
async def read_user_usage(
    user_id: int,
    user: AiAdmin,
    session: DbSession,
    window: UsageWindow = "week",
) -> UserUsageOut:
    del user
    target = await session.get(User, user_id)
    if target is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such user")
    settings_row = await platform.get_platform_settings(session)
    tz = org_zone(settings_row)
    start, end = usage_read.window_bounds(window, now=datetime.now(UTC), org_tz=tz)

    agents = await usage_read.user_agents_breakdown(session, user_id=user_id, start=start, end=end)
    chat = await usage_read.user_chat_breakdown(session, user_id=user_id, start=start, end=end)
    return UserUsageOut(
        user_id=target.id,
        full_name=target.full_name,
        email=target.email,
        agent_tokens=sum(tokens for *_, tokens in agents),
        chat_tokens=sum(tokens for _, _, tokens in chat),
        limits=_limits_out(settings_row),
        agents=[
            AgentBreakdownOut(agent_id=agent_id, name=name, model=model, runs=runs, tokens=tokens)
            for agent_id, name, model, runs, tokens in agents
        ],
        chat=[
            ChatBreakdownOut(model=model, messages=messages, tokens=tokens)
            for model, messages, tokens in chat
        ],
    )
