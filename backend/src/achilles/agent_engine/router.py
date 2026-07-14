"""Agent API: owner CRUD + run + journal; admin registry + pause + limits.

Contract: agent-engine/index.html#api. Agent Engine owns it; Web App and
Admin Panel are thin clients. A foreign agent answers 404 to its owner-side
routes (existence is not disclosed); the admin's only lever over a foreign
agent is the pause toggle (governance.html#admin-pause).
"""

from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, status

from achilles.agent_engine import runs, service
from achilles.agent_engine.constants import AgentStatus
from achilles.agent_engine.models import Agent, AgentRun, AgentTool
from achilles.agent_engine.runtime.tools import CORE_TOOL_NAMES
from achilles.agent_engine.schemas import (
    AdminAgentDetailOut,
    AdminAgentOut,
    AdminPauseIn,
    AgentCreate,
    AgentLimitsOut,
    AgentLimitsPatch,
    AgentListOut,
    AgentModelOptionOut,
    AgentOptionsOut,
    AgentOut,
    AgentOwnerOut,
    AgentPatch,
    AgentToolOptionOut,
    BudgetOut,
    LastRunOut,
    RunOut,
    RunStarted,
    parse_schedule,
)
from achilles.ai_foundation.models import AgentModel, AiModel, Tool
from achilles.api.background import publish_lane
from achilles.api.pagination import (
    DEFAULT_PAGE_SIZE,
    CursorParam,
    LimitParam,
    OffsetPage,
    OffsetParams,
    Page,
    keyset_page,
    offset_page,
)
from achilles.auth.constants import Permission
from achilles.auth.dependencies import CurrentUser, require
from achilles.auth.models import User
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.lifecycle import run_duration_seconds
from achilles.infra.worker.base import Lane
from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services.platform import SINGLETON_ID, get_platform_settings
from achilles.notifications.api import dispatch_from_request

router = APIRouter(prefix="/agents", tags=["agents"])
admin_router = APIRouter(prefix="/admin/agents", tags=["agents-admin"])
limits_router = APIRouter(prefix="/admin/agent-limits", tags=["agents-admin"])

AiAdmin = Annotated[User, require(Permission.AI_ADMIN)]


def _duration_seconds(run: AgentRun) -> int | None:
    seconds = run_duration_seconds(run.started_at, run.finished_at)
    return int(seconds) if seconds is not None else None


def _last_run_out(run: AgentRun | None) -> LastRunOut | None:
    if run is None:
        return None
    return LastRunOut(
        state=run.state,
        reason=run.reason,
        finished_at=run.finished_at or run.created_at,
        duration_seconds=_duration_seconds(run),
        tokens_used=run.tokens_used,
    )


def _agent_out(
    agent: Agent,
    *,
    over_budget: bool,
    tool_ids: list[int],
    last_run: AgentRun | None,
    model_live: bool,
    disabled_tools: list[AgentToolOptionOut] | None = None,
) -> AgentOut:
    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        prompt=agent.prompt,
        schedule=parse_schedule(agent.schedule),
        model_id=agent.model_id,
        enabled=agent.enabled,
        admin_paused=agent.admin_paused,
        status=service.derive_status(agent, over_budget=over_budget, model_live=model_live),
        tool_ids=tool_ids,
        disabled_tools=disabled_tools or [],
        next_run_at=agent.next_run_at,
        last_run=_last_run_out(last_run),
        created_at=agent.created_at,
    )


def _run_out(run: AgentRun) -> RunOut:
    return RunOut(
        id=run.id,
        trigger=run.trigger,
        state=run.state,
        reason=run.reason,
        output=run.output,
        tokens_used=run.tokens_used,
        error=run.error,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=_duration_seconds(run),
        created_at=run.created_at,
    )


async def _owner_agent_out(
    session: DbSession, agent: Agent, *, user_id: int, platform_row: PlatformSettings, now: datetime
) -> AgentOut:
    """One agent through the owner's eyes — the shared tail of create/get/patch."""
    used, limit, _ = await service.budget_snapshot(
        session, user_id=user_id, platform=platform_row, now=now
    )
    tools_map = await service.tool_ids_map(session, [agent.id])
    tool_ids = tools_map.get(agent.id, [])
    disabled = await service.disabled_tools(session, tool_ids)
    last_map = await runs.last_run_map(session, [agent.id])
    return _agent_out(
        agent,
        over_budget=service.budget_exceeded(used, limit),
        tool_ids=tool_ids,
        disabled_tools=[AgentToolOptionOut(id=row_id, name=name) for row_id, name in disabled],
        last_run=last_map.get(agent.id),
        model_live=await service.model_is_live(session, agent.model_id),
    )


# --- Owner routes ---


@router.get("/options")
async def agent_options(user: CurrentUser, session: DbSession) -> AgentOptionsOut:
    """The editor's selects: allowed models + tools available to agents."""
    del user
    model_rows = await session.execute(
        sa.select(AgentModel.id, AiModel.display_name, AgentModel.is_default)
        .join(AiModel, AiModel.id == AgentModel.model_id)
        .where(AiModel.is_enabled, AgentModel.is_enabled)
        .order_by(AgentModel.id)
    )
    tool_rows = await session.execute(
        sa.select(Tool.id, Tool.name).where(Tool.agents_allowed).order_by(Tool.id)
    )
    return AgentOptionsOut(
        models=[
            AgentModelOptionOut(id=row_id, display_name=name, is_default=is_default)
            for row_id, name, is_default in model_rows
        ],
        tools=[AgentToolOptionOut(id=row_id, name=name) for row_id, name in tool_rows],
        core_tools=list(CORE_TOOL_NAMES),
    )


@router.get("")
async def list_agents(user: CurrentUser, session: DbSession) -> AgentListOut:
    now = datetime.now(UTC)
    platform_row = await get_platform_settings(session)
    used, limit, resets_at = await service.budget_snapshot(
        session, user_id=user.id, platform=platform_row, now=now
    )
    over = service.budget_exceeded(used, limit)
    agents = (
        await session.scalars(
            sa.select(Agent).where(Agent.user_id == user.id).order_by(Agent.id.desc())
        )
    ).all()
    agent_ids = [agent.id for agent in agents]
    tools_map = await service.tool_ids_map(session, agent_ids)
    last_map = await runs.last_run_map(session, agent_ids)
    live_ids = await service.live_model_ids(session, {agent.model_id for agent in agents})
    return AgentListOut(
        items=[
            _agent_out(
                agent,
                over_budget=over,
                tool_ids=tools_map.get(agent.id, []),
                last_run=last_map.get(agent.id),
                model_live=agent.model_id in live_ids,
            )
            for agent in agents
        ],
        budget=BudgetOut(used=used, limit=limit, week_resets_at=resets_at),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, user: CurrentUser, session: DbSession) -> AgentOut:
    now = datetime.now(UTC)
    platform_row = await get_platform_settings(session)
    agent = await service.create_agent(
        session, user=user, body=body, platform=platform_row, now=now
    )
    await session.commit()
    return await _owner_agent_out(
        session, agent, user_id=user.id, platform_row=platform_row, now=now
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: int, user: CurrentUser, session: DbSession) -> AgentOut:
    agent = await service.get_owned(session, user_id=user.id, agent_id=agent_id)
    platform_row = await get_platform_settings(session)
    return await _owner_agent_out(
        session, agent, user_id=user.id, platform_row=platform_row, now=datetime.now(UTC)
    )


@router.patch("/{agent_id}")
async def patch_agent(
    agent_id: int, body: AgentPatch, user: CurrentUser, session: DbSession
) -> AgentOut:
    agent = await service.get_owned(session, user_id=user.id, agent_id=agent_id)
    now = datetime.now(UTC)
    platform_row = await get_platform_settings(session)
    agent = await service.patch_agent(
        session, user=user, agent=agent, body=body, platform=platform_row, now=now
    )
    await session.commit()
    return await _owner_agent_out(
        session, agent, user_id=user.id, platform_row=platform_row, now=now
    )


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: int, user: CurrentUser, session: DbSession) -> None:
    agent = await service.get_owned(session, user_id=user.id, agent_id=agent_id)
    await session.delete(agent)  # CASCADE clears agent_runs + agent_tools
    await session.commit()


@router.post("/{agent_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_agent_now(
    agent_id: int, user: CurrentUser, request: Request, session: DbSession
) -> RunStarted:
    agent = await service.get_owned(session, user_id=user.id, agent_id=agent_id)
    platform_row = await get_platform_settings(session)
    run_id = await service.gate_manual_run(
        session, agent=agent, platform=platform_row, now=datetime.now(UTC)
    )
    await session.commit()
    await publish_board(request.state.redis.cache, Board.AGENTS, user_id=user.id)  # queued
    await publish_lane(request, Lane.AGENTS, "run_agent", job_id=f"agent:{run_id}", run_id=run_id)
    return RunStarted(run_id=run_id)


@router.get("/{agent_id}/runs")
async def list_runs(
    agent_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: LimitParam = DEFAULT_PAGE_SIZE,
    cursor: CursorParam = None,
) -> Page[RunOut]:
    agent = await service.get_owned(session, user_id=user.id, agent_id=agent_id)
    rows, next_cursor = await keyset_page(
        session, runs.runs_query(agent.id), AgentRun.id, limit=limit, cursor=cursor, descending=True
    )
    return Page(items=[_run_out(run) for run in rows], next_cursor=next_cursor)


# --- Admin routes (governance.html#admin-pause) ---


async def _owner_map(session: DbSession, user_ids: list[int]) -> dict[int, AgentOwnerOut]:
    """Owner cards for a page of agents — one batched query, not N."""
    if not user_ids:
        return {}
    rows = await session.execute(
        sa.select(User.id, User.email, User.full_name).where(User.id.in_(user_ids))
    )
    return {
        owner_id: AgentOwnerOut(id=owner_id, email=email, display_name=full_name)
        for owner_id, email, full_name in rows
    }


def _admin_agent_out(
    agent: Agent,
    *,
    owner: AgentOwnerOut,
    over_budget: bool,
    last_run: AgentRun | None,
    model_live: bool,
) -> AdminAgentOut:
    return AdminAgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        schedule=parse_schedule(agent.schedule),
        enabled=agent.enabled,
        admin_paused=agent.admin_paused,
        status=service.derive_status(agent, over_budget=over_budget, model_live=model_live),
        owner=owner,
        last_run=_last_run_out(last_run),
        created_at=agent.created_at,
    )


@admin_router.get("")
async def admin_list_agents(
    user: AiAdmin,
    session: DbSession,
    params: Annotated[OffsetParams, Depends()],
    *,
    q: str | None = None,
    status: Annotated[list[AgentStatus] | None, Query()] = None,
    scheduled: bool | None = None,
) -> OffsetPage[AdminAgentOut]:
    del user
    now = datetime.now(UTC)
    platform_row = await get_platform_settings(session)
    stmt = service.admin_agents_query(q=q, scheduled=scheduled)
    if status:
        # Statuses combine as OR — the derived-status ladder's SQL twin, folding
        # in the per-owner weekly spend for the budget-dependent states.
        stmt = stmt.where(
            service.status_filter_clause(
                status,
                budget_limit=platform_row.agent_weekly_token_budget,
                window_start=service.weekly_window_start(now, service.org_zone(platform_row)),
            )
        )
    rows, total, page = await offset_page(session, stmt, params)
    agent_ids = [agent.id for agent in rows]
    owner_ids = list({agent.user_id for agent in rows})
    last_map = await runs.last_run_map(session, agent_ids)
    owners = await _owner_map(session, owner_ids)
    live_ids = await service.live_model_ids(session, {agent.model_id for agent in rows})
    spend_map = await service.weekly_spend_map(
        session,
        user_ids=owner_ids,
        since=service.weekly_window_start(now, service.org_zone(platform_row)),
    )
    budget_limit = platform_row.agent_weekly_token_budget
    items = [
        _admin_agent_out(
            agent,
            owner=owners[agent.user_id],
            over_budget=service.budget_exceeded(spend_map.get(agent.user_id, 0), budget_limit),
            last_run=last_map.get(agent.id),
            model_live=agent.model_id in live_ids,
        )
        for agent in rows
    ]
    return OffsetPage(items=items, total=total, page=page, per_page=params.per_page)


async def _admin_detail(
    session: DbSession, agent: Agent, *, platform_row: PlatformSettings, now: datetime
) -> AdminAgentDetailOut:
    used, limit, resets_at = await service.budget_snapshot(
        session, user_id=agent.user_id, platform=platform_row, now=now
    )
    over = service.budget_exceeded(used, limit)
    last_map = await runs.last_run_map(session, [agent.id])
    owners = await _owner_map(session, [agent.user_id])
    model_name = None
    if agent.model_id is not None:
        model_name = await session.scalar(
            sa.select(AiModel.display_name)
            .join(AgentModel, AgentModel.model_id == AiModel.id)
            .where(AgentModel.id == agent.model_id)
        )
    tool_rows = await session.execute(
        sa.select(Tool.id, Tool.name)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id)
        .order_by(Tool.id)
    )
    base = _admin_agent_out(
        agent,
        owner=owners[agent.user_id],
        over_budget=over,
        last_run=last_map.get(agent.id),
        model_live=await service.model_is_live(session, agent.model_id),
    )
    return AdminAgentDetailOut(
        **base.model_dump(),
        prompt=agent.prompt,
        model_name=model_name,
        tools=[AgentToolOptionOut(id=row_id, name=name) for row_id, name in tool_rows],
        next_run_at=agent.next_run_at,
        owner_budget=BudgetOut(used=used, limit=limit, week_resets_at=resets_at),
    )


@admin_router.get("/{agent_id}")
async def admin_get_agent(agent_id: int, user: AiAdmin, session: DbSession) -> AdminAgentDetailOut:
    del user
    agent = await service.get_any(session, agent_id)
    platform_row = await get_platform_settings(session)
    return await _admin_detail(session, agent, platform_row=platform_row, now=datetime.now(UTC))


@admin_router.get("/{agent_id}/runs")
async def admin_list_runs(
    agent_id: int,
    user: AiAdmin,
    session: DbSession,
    limit: LimitParam = DEFAULT_PAGE_SIZE,
    cursor: CursorParam = None,
) -> Page[RunOut]:
    del user
    agent = await service.get_any(session, agent_id)
    rows, next_cursor = await keyset_page(
        session, runs.runs_query(agent.id), AgentRun.id, limit=limit, cursor=cursor, descending=True
    )
    return Page(items=[_run_out(run) for run in rows], next_cursor=next_cursor)


@admin_router.patch("/{agent_id}/pause")
async def admin_set_pause(
    agent_id: int, body: AdminPauseIn, user: AiAdmin, request: Request, session: DbSession
) -> AdminAgentDetailOut:
    agent = await service.get_any(session, agent_id)
    now = datetime.now(UTC)
    platform_row = await get_platform_settings(session)
    owner_tz = await service.owner_zone(
        session, agent.user_id, fallback=service.org_zone(platform_row)
    )
    agent = await service.set_admin_pause(
        session, agent=agent, paused=body.paused, owner_tz=owner_tz, now=now
    )
    await record_audit(
        request,
        action=AuditAction.AGENT_PAUSE,
        actor_id=user.id,
        target_type="agent",
        target_id=str(agent.id),
        meta={"paused": body.paused},
    )
    await session.commit()
    if body.paused:
        # Lifting the pause is the owner's good news in the editor banner, not a feed event.
        await dispatch_from_request(
            request,
            session,
            event="agent.admin_paused",
            target_user_id=agent.user_id,
            source_ref=f"agent/{agent.id}",
            params={"agent_name": agent.name},
        )
    return await _admin_detail(session, agent, platform_row=platform_row, now=now)


@limits_router.get("")
async def get_agent_limits(user: AiAdmin, session: DbSession) -> AgentLimitsOut:
    del user
    platform_row = await get_platform_settings(session)
    return AgentLimitsOut(
        iteration_cap=platform_row.agent_iteration_cap,
        max_concurrency=platform_row.agent_max_concurrency,
    )


@limits_router.patch("")
async def patch_agent_limits(
    body: AgentLimitsPatch, user: AiAdmin, request: Request, session: DbSession
) -> AgentLimitsOut:
    platform_row = await get_platform_settings(session)
    if body.iteration_cap is not None:
        platform_row.agent_iteration_cap = body.iteration_cap
    if body.max_concurrency is not None:
        platform_row.agent_max_concurrency = body.max_concurrency
    await record_audit(
        request,
        action=AuditAction.AGENT_LIMITS_UPDATE,
        actor_id=user.id,
        target_type="platform_settings",
        target_id=str(SINGLETON_ID),
        meta=body.model_dump(exclude_unset=True),
    )
    await session.commit()
    return AgentLimitsOut(
        iteration_cap=platform_row.agent_iteration_cap,
        max_concurrency=platform_row.agent_max_concurrency,
    )
