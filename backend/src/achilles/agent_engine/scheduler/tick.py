"""Cron body on the scheduler singleton: the next_run_at scan (execution.html#schedule).

Publish-only, like every tick. The scan key next_run_at already encodes the
durable gate (NULL when disabled / admin-locked / model-less / manual-only);
the sweep phase re-nulls rows whose gate closed *behind the code's back* —
the one such path is agent_models deletion, whose SET NULL runs inside
Postgres and recomputes nothing.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import runs, service
from achilles.agent_engine.constants import AgentRunReason, AgentRunTrigger
from achilles.agent_engine.models import Agent
from achilles.api.problems import ApiError
from achilles.config import settings as app_settings
from achilles.db.connections import close_connections, create_connections
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.infra.worker.base import Lane, publish
from achilles.knowledge_store.services import platform
from achilles.notifications.api import dispatch_from_tick


async def agents_tick(ctx: Context) -> None:
    """Sweep closed gates, then start every agent whose slot has come."""
    del ctx
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        run_ids: list[int] = []
        async with db.pg_session_factory() as session:
            await _sweep_closed_gates(session)
            # Scalars, not the ORM row: a rollback mid-loop expires instances,
            # and an async session cannot lazily refresh them.
            platform_row = await platform.get_platform_settings(session)
            if platform_row.maintenance_mode:
                return  # org maintenance pauses scheduled launches, not running work
            budget_limit = platform_row.agent_weekly_token_budget
            org_tz = service.org_zone(platform_row)
            now = datetime.now(UTC)
            due_ids = list(
                await session.scalars(
                    sa.select(Agent.id).where(Agent.next_run_at <= now).order_by(Agent.id)
                )
            )
            for agent_id in due_ids:
                run_id = await _start_due_agent(
                    session, redis, agent_id, budget_limit=budget_limit, org_tz=org_tz, now=now
                )
                if run_id is not None:
                    run_ids.append(run_id)
        for run_id in run_ids:
            await publish(
                app_settings.redis_durable_url,
                redis.durable,
                Lane.AGENTS,
                "run_agent",
                job_id=f"agent:{run_id}",
                run_id=run_id,
            )
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def _sweep_closed_gates(session: AsyncSession) -> None:
    """next_run_at → NULL where the durable gate is closed (quiet, no journal rows)."""
    await session.execute(
        sa.update(Agent)
        .where(Agent.next_run_at.is_not(None), service.durable_stop_clause())
        .values(next_run_at=None)
    )
    await session.commit()


async def _start_due_agent(
    session: AsyncSession,
    redis: RedisPools,
    agent_id: int,
    *,
    budget_limit: int | None,
    org_tz: ZoneInfo,
    now: datetime,
) -> int | None:
    """One agent, one transaction: journal row + next_run_at advance; id to publish.

    The slot advances on every attempt — skipped included — or an exhausted
    budget would re-fire the same agent every scan minute.
    """
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.next_run_at is None:
        return None
    owner_tz = await service.owner_zone(session, agent.user_id, fallback=org_tz)
    model_live = await service.model_is_live(session, agent.model_id)
    fired_slot = agent.next_run_at
    used = await service.weekly_spend(
        session, user_id=agent.user_id, since=service.weekly_window_start(now, org_tz)
    )
    if service.budget_exceeded(used, budget_limit):
        owner_id, agent_name, agent_pk = agent.user_id, agent.name, agent.id
        await _skip_and_advance(
            session,
            agent,
            reason=AgentRunReason.BUDGET_EXCEEDED,
            owner_tz=owner_tz,
            now=now,
            fired_slot=fired_slot,
            model_live=model_live,
        )
        await publish_board(redis.cache, Board.AGENTS, user_id=owner_id)  # skipped row landed
        week = service.weekly_window_start(now, org_tz).date().isoformat()
        await dispatch_from_tick(
            session,
            redis,
            event="agent.budget_exhausted",
            target_user_id=owner_id,
            source_ref=f"agent/{agent_pk}",
            params={"agent_name": agent_name},
            # one note per owner per week, however many agents get skipped
            dedup_key=f"agentbudget:{owner_id}:{week}",
        )
        return None
    try:
        run_id = await runs.start_run(
            session, agent_id=agent.id, trigger=str(AgentRunTrigger.SCHEDULED)
        )
    except ApiError:
        await session.rollback()  # expires loaded instances — re-get below
        agent = await session.get(Agent, agent_id)
        if agent is None:
            return None
        owner_id = agent.user_id
        await _skip_and_advance(
            session,
            agent,
            reason=AgentRunReason.ALREADY_RUNNING,
            owner_tz=owner_tz,
            now=now,
            fired_slot=fired_slot,
            model_live=model_live,
        )
        await publish_board(redis.cache, Board.AGENTS, user_id=owner_id)  # skipped row landed
        return None
    owner_id = agent.user_id
    service.recompute_next_run(
        agent, owner_tz=owner_tz, now=now, base=fired_slot, model_live=model_live
    )
    await session.commit()
    await publish_board(redis.cache, Board.AGENTS, user_id=owner_id)  # a queued row appeared
    return run_id


async def _skip_and_advance(
    session: AsyncSession,
    agent: Agent,
    *,
    reason: AgentRunReason,
    owner_tz: ZoneInfo,
    now: datetime,
    fired_slot: datetime,
    model_live: bool,
) -> None:
    """Journal the refusal and still advance the slot — the shared skip tail."""
    await runs.insert_skipped(
        session, agent_id=agent.id, trigger=str(AgentRunTrigger.SCHEDULED), reason=reason
    )
    service.recompute_next_run(
        agent, owner_tz=owner_tz, now=now, base=fired_slot, model_live=model_live
    )
    await session.commit()
