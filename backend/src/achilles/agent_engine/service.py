"""Agent service layer: owner CRUD, the start gate, the derived weekly budget.

Design: agent-engine/_workzone/governance.html. The gate has four independent
conditions (enabled ∧ ¬admin_paused ∧ budget within ∧ model present); an empty
knowledge base is soft degradation, never a gate. The budget is derived —
SUM over the journal, no counter table (data-model.html#agent-runs).
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from achilles.agent_engine import runs
from achilles.agent_engine.constants import (
    CODE_AGENT_BUDGET_EXCEEDED,
    CODE_AGENT_NOT_RUNNABLE,
    WEEK_RESET_WEEKDAY,
    AgentRunReason,
    AgentRunTrigger,
    AgentStatus,
)
from achilles.agent_engine.models import Agent, AgentRun, AgentTool
from achilles.agent_engine.scheduler.slots import next_slot
from achilles.agent_engine.schemas import (
    AgentCreate,
    AgentPatch,
    ScheduleSpec,
    parse_schedule,
)
from achilles.ai_foundation.constants import CheckStatus
from achilles.ai_foundation.models import AgentModel, AiModel, AiProvider, Tool
from achilles.api.problems import CODE_NOT_FOUND, CODE_VALIDATION_ERROR, ApiError
from achilles.auth.models import User
from achilles.infra.scheduler.cron import safe_zone
from achilles.knowledge_store.models import PlatformSettings

WEEK = timedelta(days=7)


# --- Timezones ---


def org_zone(platform: PlatformSettings) -> ZoneInfo:
    return safe_zone(platform.timezone)


# --- Weekly budget (derived, governance.html#budget) ---


def weekly_window_start(now: datetime, org_tz: ZoneInfo) -> datetime:
    """Most recent week-reset midnight (WEEK_RESET_WEEKDAY) in org time, as UTC."""
    local = now.astimezone(org_tz)
    days_since_reset = (local.weekday() - WEEK_RESET_WEEKDAY) % 7
    start_local = (local - timedelta(days=days_since_reset)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start_local.astimezone(UTC)


async def weekly_spend_map(
    session: AsyncSession, *, user_ids: list[int], since: datetime
) -> dict[int, int]:
    """Tokens finished inside the window per owner — one grouped query, not N.

    Filtered on finished_at: a running spend joins the sum only at its finale —
    the narrow overrun is accepted for v1 (data-model.html#agent-runs).
    """
    if not user_ids:
        return {}
    rows = await session.execute(
        sa.select(Agent.user_id, sa.func.sum(AgentRun.tokens_used))
        .join(AgentRun, AgentRun.agent_id == Agent.id)
        .where(Agent.user_id.in_(user_ids), AgentRun.finished_at >= since)
        .group_by(Agent.user_id)
    )
    return {int(user_id): int(total or 0) for user_id, total in rows}


async def weekly_spend(session: AsyncSession, *, user_id: int, since: datetime) -> int:
    """One owner's window total — the single-owner cut of weekly_spend_map."""
    spend = await weekly_spend_map(session, user_ids=[user_id], since=since)
    return spend.get(user_id, 0)


async def budget_snapshot(
    session: AsyncSession, *, user_id: int, platform: PlatformSettings, now: datetime
) -> tuple[int, int | None, datetime]:
    """(used, limit, week_resets_at) for the owner."""
    tz = org_zone(platform)
    window_start = weekly_window_start(now, tz)
    used = await weekly_spend(session, user_id=user_id, since=window_start)
    # +7 days in LOCAL wall-clock time: a fixed-UTC week drifts an hour off
    # the next local midnight when a DST shift falls inside the window.
    resets_at = (window_start.astimezone(tz) + WEEK).astimezone(UTC)
    return used, platform.agent_weekly_token_budget, resets_at


def budget_exceeded(used: int, limit: int | None) -> bool:
    return limit is not None and used >= limit


# --- Start gate + derived status (governance.html#gate) ---


def _model_live_clause() -> sa.ColumnElement[bool]:
    """True when Agent.model_id resolves to a runnable model.

    "Model present" is not "model_id ≠ NULL": an allow-list entry paused
    (is_enabled=false, row kept) or its catalog model disabled is unrunnable —
    jobs._prepare fails it. The gate/status must see the same, or a paused-model
    agent reads "Active" yet every run fails. A NULL model_id matches no row.
    Provider health counts too (ai-models.html#5: "when a provider errors, its
    models are unavailable"): a model whose provider is in ``error`` is unrunnable,
    so it degrades to MODEL_MISSING instead of showing "Active" and failing.
    """
    return sa.exists(
        sa.select(sa.literal(1))
        .select_from(AgentModel)
        .join(AiModel, AiModel.id == AgentModel.model_id)
        .join(AiProvider, AiProvider.id == AiModel.provider_id)
        .where(
            AgentModel.id == Agent.model_id,
            AgentModel.is_enabled,
            AiModel.is_enabled,
            AiProvider.status != CheckStatus.ERROR.value,
        )
    )


async def model_is_live(session: AsyncSession, model_id: int | None) -> bool:
    """Python twin of _model_live_clause for a single loaded agent's model_id."""
    if model_id is None:
        return False
    found = await session.scalar(
        sa.select(AgentModel.id)
        .join(AiModel, AiModel.id == AgentModel.model_id)
        .join(AiProvider, AiProvider.id == AiModel.provider_id)
        .where(
            AgentModel.id == model_id,
            AgentModel.is_enabled,
            AiModel.is_enabled,
            AiProvider.status != CheckStatus.ERROR.value,
        )
    )
    return found is not None


async def live_model_ids(session: AsyncSession, model_ids: set[int | None]) -> set[int]:
    """The subset of model_ids that resolve to a runnable model — one query for a page."""
    ids = [model_id for model_id in model_ids if model_id is not None]
    if not ids:
        return set()
    rows = await session.scalars(
        sa.select(AgentModel.id)
        .join(AiModel, AiModel.id == AgentModel.model_id)
        .join(AiProvider, AiProvider.id == AiModel.provider_id)
        .where(
            AgentModel.id.in_(ids),
            AgentModel.is_enabled,
            AiModel.is_enabled,
            AiProvider.status != CheckStatus.ERROR.value,
        )
    )
    return set(rows)


def durable_stop(agent: Agent, *, model_live: bool) -> bool:
    """The three durable gate conditions; a stop here leaves no journal noise."""
    return not agent.enabled or agent.admin_paused or not model_live


def durable_stop_clause() -> sa.ColumnElement[bool]:
    """SQL twin of durable_stop — the tick's sweep must see the same conditions."""
    return sa.or_(~Agent.enabled, Agent.admin_paused, sa.not_(_model_live_clause()))


def derive_status(agent: Agent, *, over_budget: bool, model_live: bool) -> AgentStatus:
    if agent.admin_paused:
        return AgentStatus.ADMIN_PAUSED
    if not agent.enabled:
        return AgentStatus.DISABLED
    if not model_live:
        return AgentStatus.MODEL_MISSING
    if over_budget:
        return AgentStatus.BUDGET_EXCEEDED
    return AgentStatus.ACTIVE


def recompute_next_run(
    agent: Agent,
    *,
    owner_tz: ZoneInfo,
    now: datetime,
    base: datetime | None = None,
    model_live: bool,
) -> None:
    """Refresh the scheduler scan key; NULL for manual-only or a closed gate."""
    schedule = parse_schedule(agent.schedule)
    if schedule is None or durable_stop(agent, model_live=model_live):
        agent.next_run_at = None
        return
    agent.next_run_at = next_slot(schedule, tz=owner_tz, now=now, base=base)


async def owner_zone(session: AsyncSession, user_id: int, *, fallback: ZoneInfo) -> ZoneInfo:
    tz_name = await session.scalar(sa.select(User.timezone).where(User.id == user_id))
    return safe_zone(tz_name, fallback)


# --- Owner CRUD ---


async def get_owned(session: AsyncSession, *, user_id: int, agent_id: int) -> Agent:
    """A foreign agent is a 404, not a 403 — existence is not disclosed."""
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.user_id != user_id:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such agent")
    return agent


async def _validate_model_id(session: AsyncSession, model_id: int) -> None:
    live = await session.scalar(
        sa.select(AgentModel.id).where(AgentModel.id == model_id, AgentModel.is_enabled)
    )
    if live is None:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            "model_id is not in the agent models list",
            errors=[{"field": "model_id", "message": "not in the allowed list"}],
        )


async def _validate_tool_ids(
    session: AsyncSession, tool_ids: list[int], *, grandfathered: set[int] | None = None
) -> None:
    # An admin can revoke agents_allowed after an agent already selected a tool.
    # Such ids are grandfathered — kept, shown disabled in the editor — so re-saving
    # the agent must not reject them; only *newly* added ids face the allow-list.
    if not tool_ids:
        return
    allowed = set(
        await session.scalars(sa.select(Tool.id).where(Tool.id.in_(tool_ids), Tool.agents_allowed))
    )
    allowed |= grandfathered or set()
    unknown = [t for t in tool_ids if t not in allowed]
    if unknown:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            "tool_ids contain tools not allowed for agents",
            errors=[{"field": "tool_ids", "message": f"not allowed: {unknown}"}],
        )


async def default_model_id(session: AsyncSession) -> int | None:
    return await session.scalar(
        sa.select(AgentModel.id).where(AgentModel.is_default, AgentModel.is_enabled)
    )


def _dump_schedule(schedule: ScheduleSpec | None) -> dict[str, Any] | None:
    return schedule.model_dump(mode="json") if schedule is not None else None


async def create_agent(
    session: AsyncSession,
    *,
    user: User,
    body: AgentCreate,
    platform: PlatformSettings,
    now: datetime,
) -> Agent:
    model_id = body.model_id
    if model_id is not None:
        await _validate_model_id(session, model_id)
    else:
        model_id = await default_model_id(session)  # preset, may stay None
    await _validate_tool_ids(session, body.tool_ids)
    agent = Agent(
        user_id=user.id,
        name=body.name,
        description=body.description,
        prompt=body.prompt,
        schedule=_dump_schedule(body.schedule),
        model_id=model_id,
    )
    session.add(agent)
    await session.flush()
    for tool_id in dict.fromkeys(body.tool_ids):
        session.add(AgentTool(agent_id=agent.id, tool_id=tool_id))
    owner_tz = safe_zone(user.timezone, org_zone(platform))
    recompute_next_run(
        agent, owner_tz=owner_tz, now=now, model_live=await model_is_live(session, model_id)
    )
    await session.flush()
    return agent


async def patch_agent(
    session: AsyncSession,
    *,
    user: User,
    agent: Agent,
    body: AgentPatch,
    platform: PlatformSettings,
    now: datetime,
) -> Agent:
    fields = body.model_fields_set
    # Validate only a *changed* selection: the editor always echoes model_id, so
    # re-validating an unchanged one would 422 any edit once its model got paused
    # — the owner must still be able to rename/reprompt a model-missing agent.
    if "model_id" in fields and body.model_id is not None and body.model_id != agent.model_id:
        await _validate_model_id(session, body.model_id)
    if "tool_ids" in fields and body.tool_ids is not None:
        existing = set(
            await session.scalars(
                sa.select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id)
            )
        )
        await _validate_tool_ids(session, body.tool_ids, grandfathered=existing)
        await session.execute(sa.delete(AgentTool).where(AgentTool.agent_id == agent.id))
        for tool_id in dict.fromkeys(body.tool_ids):
            session.add(AgentTool(agent_id=agent.id, tool_id=tool_id))
    old_model_id = agent.model_id
    model_live = await model_is_live(session, agent.model_id)
    before = (durable_stop(agent, model_live=model_live), agent.schedule)
    for field in ("name", "description", "prompt", "model_id", "enabled"):
        if field in fields:
            setattr(agent, field, getattr(body, field))
    if "schedule" in fields:
        agent.schedule = _dump_schedule(body.schedule)
    # Re-read liveness only when the selection actually moved; a rename or
    # reprompt leaves the model untouched, so the pre-loop lookup still holds.
    if agent.model_id != old_model_id:
        model_live = await model_is_live(session, agent.model_id)
    # Recompute only when the gate or the schedule actually moved: an interval
    # slot anchors to the previous start (slots.py), so a rename or prompt edit
    # must not re-anchor it to "now" and postpone an imminent run.
    if (durable_stop(agent, model_live=model_live), agent.schedule) != before:
        owner_tz = safe_zone(user.timezone, org_zone(platform))
        recompute_next_run(agent, owner_tz=owner_tz, now=now, model_live=model_live)
    await session.flush()
    return agent


async def tool_ids_map(session: AsyncSession, agent_ids: list[int]) -> dict[int, list[int]]:
    if not agent_ids:
        return {}
    rows = await session.execute(
        sa.select(AgentTool.agent_id, AgentTool.tool_id)
        .where(AgentTool.agent_id.in_(agent_ids))
        .order_by(AgentTool.id)
    )
    out: dict[int, list[int]] = {}
    for agent_id, tool_id in rows:
        out.setdefault(agent_id, []).append(tool_id)
    return out


async def disabled_tools(session: AsyncSession, tool_ids: list[int]) -> list[tuple[int, str]]:
    """Of the agent's selected tools, those an admin has since disallowed for agents.

    The runtime already skips them (runtime/tools.py filters agents_allowed); the
    editor surfaces them as disabled pills rather than dropping them silently.
    """
    if not tool_ids:
        return []
    rows = await session.execute(
        sa.select(Tool.id, Tool.name)
        .where(Tool.id.in_(tool_ids), sa.not_(Tool.agents_allowed))
        .order_by(Tool.id)
    )
    return [(row_id, name) for row_id, name in rows]


# --- Manual run (execution.html#schedule) ---


async def gate_manual_run(
    session: AsyncSession, *, agent: Agent, platform: PlatformSettings, now: datetime
) -> int:
    """The owner's "Run now": gate → journal → id of the new queued row.

    A durable stop (disabled / admin lock / no model) → 409 without a row;
    the runtime gates (budget, overlap) journal a skipped row and still 409 —
    the refusal must be visible where the owner looks (execution.html#schedule).
    """
    # Plain ids up front: the rollback below expires loaded instances, and an
    # async session cannot lazily refresh them.
    agent_id, owner_id = agent.id, agent.user_id
    if durable_stop(agent, model_live=await model_is_live(session, agent.model_id)):
        raise ApiError(
            409,
            CODE_AGENT_NOT_RUNNABLE,
            "Agent is not runnable",
            "The agent is disabled, admin-paused or has no model.",
        )
    used, limit, _ = await budget_snapshot(session, user_id=owner_id, platform=platform, now=now)
    if budget_exceeded(used, limit):
        await runs.insert_skipped(
            session,
            agent_id=agent_id,
            trigger=str(AgentRunTrigger.MANUAL),
            reason=AgentRunReason.BUDGET_EXCEEDED,
        )
        await session.commit()
        raise ApiError(
            409,
            CODE_AGENT_BUDGET_EXCEEDED,
            "Weekly token budget exceeded",
            "The owner's weekly agent budget is exhausted; runs resume after the reset.",
        )
    try:
        return await runs.start_run(session, agent_id=agent_id, trigger=str(AgentRunTrigger.MANUAL))
    except ApiError:
        await session.rollback()
        await runs.insert_skipped(
            session,
            agent_id=agent_id,
            trigger=str(AgentRunTrigger.MANUAL),
            reason=AgentRunReason.ALREADY_RUNNING,
        )
        await session.commit()
        raise


# --- Admin (governance.html#admin-pause) ---


async def get_any(session: AsyncSession, agent_id: int) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such agent")
    return agent


def admin_agents_query(
    *,
    q: str | None = None,
    scheduled: bool | None = None,
) -> sa.Select[tuple[Agent]]:
    """Agents only (keyset-friendly); the User join serves the search filter."""
    stmt = sa.select(Agent).join(User, User.id == Agent.user_id).order_by(Agent.id.desc())
    if q:
        # autoescape neutralizes % and _ in the needle — LIKE metacharacters
        # typed into the search box must match literally.
        stmt = stmt.where(
            sa.or_(
                Agent.name.icontains(q, autoescape=True),
                User.email.icontains(q, autoescape=True),
            )
        )
    if scheduled is not None:
        manual = no_schedule_clause()
        stmt = stmt.where(sa.not_(manual) if scheduled else manual)
    return stmt


def no_schedule_clause() -> sa.ColumnElement[bool]:
    """Manual = no schedule.

    Stored either as SQL NULL or a JSONB ``'null'`` literal (seed rows), both of
    which load back as Python ``None`` — the filter must catch both, or manual
    agents fall through ``schedule IS NULL``.
    """
    return sa.or_(Agent.schedule.is_(None), sa.func.jsonb_typeof(Agent.schedule) == "null")


def status_filter_clause(
    statuses: Sequence[AgentStatus], *, budget_limit: int | None, window_start: datetime
) -> sa.ColumnElement[bool]:
    """OR of the requested derived statuses — the SQL twin of ``derive_status``.

    Mirrors the same priority ladder, so each row matches exactly one status;
    ``budget_exceeded`` / ``active`` fold in a correlated per-owner spend sum
    (finished tokens inside the weekly window) against the platform budget.
    """
    inner = aliased(Agent)
    owner_spend = (
        sa.select(sa.func.coalesce(sa.func.sum(AgentRun.tokens_used), 0))
        .select_from(inner)
        .join(AgentRun, AgentRun.agent_id == inner.id)
        .where(inner.user_id == Agent.user_id, AgentRun.finished_at >= window_start)
        .correlate(Agent)
        .scalar_subquery()
    )
    over_budget = owner_spend >= budget_limit if budget_limit is not None else sa.false()
    model_live = _model_live_clause()
    healthy = sa.and_(~Agent.admin_paused, Agent.enabled, model_live)
    predicate: dict[AgentStatus, sa.ColumnElement[bool]] = {
        AgentStatus.ADMIN_PAUSED: sa.and_(Agent.admin_paused),
        AgentStatus.DISABLED: sa.and_(~Agent.admin_paused, ~Agent.enabled),
        AgentStatus.MODEL_MISSING: sa.and_(~Agent.admin_paused, Agent.enabled, sa.not_(model_live)),
        AgentStatus.BUDGET_EXCEEDED: sa.and_(healthy, over_budget),
        AgentStatus.ACTIVE: sa.and_(healthy, sa.not_(over_budget)),
    }
    return sa.or_(*(predicate[status] for status in statuses))


async def set_admin_pause(
    session: AsyncSession, *, agent: Agent, paused: bool, owner_tz: ZoneInfo, now: datetime
) -> Agent:
    """The admin's only lever over a foreign agent; sticky until the same toggle."""
    agent.admin_paused = paused
    recompute_next_run(
        agent,
        owner_tz=owner_tz,
        now=now,
        model_live=await model_is_live(session, agent.model_id),
    )
    await session.flush()
    # The owner's targeted notification is raised by the admin route after commit.
    return agent
