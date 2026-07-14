"""SAQ job: run_agent — the agents-lane body of one agent run.

Same shape as the harvester job: the worker opens its own connections from
module-level settings, journals through agent_engine.runs and beats under
heartbeat_loop. The platform concurrency ceiling is a DB gate at mark_running
(execution.html#concurrency) — a blocked run waits in queued with heartbeats,
so a PATCH of the limit applies live without a worker restart.
"""

import logging
from dataclasses import dataclass

import sqlalchemy as sa
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from achilles.agent_engine import runs
from achilles.agent_engine.constants import (
    LOOP_ROUND_MAX_TOKENS,
    AgentRunReason,
    AgentRunState,
)
from achilles.agent_engine.models import Agent, AgentRun
from achilles.agent_engine.runtime.loop import LoopOutcome, run_loop
from achilles.agent_engine.runtime.prompt import KICKOFF_MESSAGE, compose_system
from achilles.agent_engine.runtime.tools import build_agent_tools
from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.llm.factory import client_for
from achilles.ai_foundation.llm.types import ChatClient, ChatMessage
from achilles.ai_foundation.models import AgentModel, AiModel, AiProvider
from achilles.ai_foundation.services.usage import record_usage
from achilles.config import settings as app_settings
from achilles.db.connections import DbConnections, close_connections, create_connections
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.lifecycle import db_beat, heartbeat_loop, wait_for_gate
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.knowledge_store.services import platform
from achilles.notifications.api import dispatch_from_worker

logger = logging.getLogger(__name__)


async def _notify_failed_run(db: DbConnections, run_id: int) -> None:
    """Tell the owner their run failed (agent.run_failed); never fail the job over it."""
    try:
        async with db.pg_session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.state != str(AgentRunState.FAILED):
                return
            agent = await session.get(Agent, run.agent_id)
            if agent is None:
                return
            owner_id, agent_pk, agent_name = agent.user_id, agent.id, agent.name
    except Exception:
        logger.warning("failed-run notification for run %s could not be raised", run_id)
        return
    await dispatch_from_worker(
        db.pg_session_factory,
        event="agent.run_failed",
        target_user_id=owner_id,
        source_ref=f"agent/{agent_pk}",
        params={"agent_name": agent_name},
        dedup_key=f"agentrun:{agent_pk}",  # a retry storm is one feed row
    )


async def _publish_run_board(db: DbConnections, redis: RedisPools, run_id: int) -> None:
    """Nudge the run's owner board; never fail the job over it."""
    try:
        async with db.pg_session_factory() as session:
            owner_id = await session.scalar(
                sa.select(Agent.user_id)
                .join(AgentRun, AgentRun.agent_id == Agent.id)
                .where(AgentRun.id == run_id)
            )
        if owner_id is not None:
            await publish_board(redis.cache, Board.AGENTS, user_id=owner_id)
    except Exception:
        logger.warning("board nudge for agent run %s failed", run_id, exc_info=True)


@dataclass(frozen=True, slots=True)
class _RunPrep:
    agent_id: int
    user_id: int
    owner_prompt: str
    model_pk: int
    model_wire_id: str
    client: ChatClient
    iteration_cap: int


async def run_agent(ctx: Context, *, run_id: int) -> None:
    """Wait out the concurrency gate, run the loop, close the journal."""
    del ctx
    crypto_key = app_settings.derived_crypto_key()
    db = create_connections(app_settings)
    redis = create_redis_pools(app_settings)
    try:
        prep = await _prepare(db.pg_session_factory, run_id, crypto_key=crypto_key)
        if prep is None:
            return

        async def try_start(session: AsyncSession) -> bool:
            # The cap is re-read on every gate attempt, so a PATCH of the
            # limit reaches runs already waiting in the gate.
            platform_row = await platform.get_platform_settings(session)
            return await runs.mark_running(
                session, run_id, max_concurrency=platform_row.agent_max_concurrency
            )

        try:
            gate = await wait_for_gate(
                db.pg_session_factory,
                try_start=try_start,
                get_state=lambda s: runs.get_state(s, run_id),
                heartbeat=lambda s: runs.heartbeat(s, run_id),
                queued_state=str(AgentRunState.QUEUED),
            )
            if gate is False:
                logger.warning("agent run %s is no longer queued — skipping", run_id)
                return
            if gate is None:
                async with db.pg_session_factory() as session, session.begin():
                    await runs.finish(
                        session,
                        run_id,
                        state=AgentRunState.FAILED,
                        reason=AgentRunReason.ERROR,
                        error="concurrency gate wait timed out",
                    )
                return
            await publish_board(redis.cache, Board.AGENTS, user_id=prep.user_id)  # → running

            beat = db_beat(db.pg_session_factory, lambda s: runs.heartbeat(s, run_id))
            async with db.pg_session_factory() as session:
                tools = await build_agent_tools(
                    session, crypto_key=crypto_key, agent_id=prep.agent_id, user_id=prep.user_id
                )
                system = await compose_system(session, owner_prompt=prep.owner_prompt)
                async with heartbeat_loop(beat):
                    outcome = await run_loop(
                        prep.client,
                        model=prep.model_wire_id,
                        system=system,
                        messages=[ChatMessage(role="user", content=KICKOFF_MESSAGE)],
                        tools=tools,
                        iteration_cap=prep.iteration_cap,
                        max_tokens=LOOP_ROUND_MAX_TOKENS,
                    )
        finally:
            await prep.client.aclose()

        async with db.pg_session_factory() as session:
            await _close_journal(session, run_id, model_pk=prep.model_pk, outcome=outcome)
    except Exception:
        logger.exception("agent run %s failed", run_id)
        async with db.pg_session_factory() as session, session.begin():
            await runs.finish(
                session,
                run_id,
                state=AgentRunState.FAILED,
                reason=AgentRunReason.ERROR,
                error="internal error",
            )
    finally:
        # One hook for every FAILED exit (gate timeout, missing model, cap, crash).
        await _notify_failed_run(db, run_id)
        # One hook for every terminal exit: whatever path closed the journal,
        # open boards refetch — a spurious nudge costs one coalesced refetch.
        await _publish_run_board(db, redis, run_id)
        await close_redis_pools(redis)
        await close_connections(db)


async def _prepare(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, *, crypto_key: bytes
) -> _RunPrep | None:
    """Load run + agent, resolve the model to a live client, read the caps.

    The gate already vouched for model_id ≠ NULL at queue time; a model that
    vanished or got disabled since resolves here to failed/error.
    """
    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            logger.error("agent run %s not found", run_id)
            return None
        agent = await session.get(Agent, run.agent_id)
        if agent is None:
            logger.error("agent run %s: agent %s is gone", run_id, run.agent_id)
            return None

        platform_row = await platform.get_platform_settings(session)

        row = (
            await session.execute(
                sa.select(AiModel, AiProvider)
                .join(AgentModel, AgentModel.model_id == AiModel.id)
                .join(AiProvider, AiProvider.id == AiModel.provider_id)
                .where(AgentModel.id == agent.model_id, AiModel.is_enabled, AgentModel.is_enabled)
            )
        ).first()
        if row is None:
            await runs.finish(
                session,
                run_id,
                state=AgentRunState.FAILED,
                reason=AgentRunReason.ERROR,
                error="agent model is not available",
            )
            await session.commit()
            return None
        model, provider = row

        return _RunPrep(
            agent_id=agent.id,
            user_id=agent.user_id,
            owner_prompt=agent.prompt,
            model_pk=model.id,
            model_wire_id=model.model_id,
            client=client_for(provider, crypto_key=crypto_key),
            iteration_cap=platform_row.agent_iteration_cap,
        )


async def _close_journal(
    session: AsyncSession, run_id: int, *, model_pk: int, outcome: LoopOutcome
) -> None:
    """Terminal write + the spend bucket in one transaction."""
    tokens = outcome.input_tokens + outcome.output_tokens
    hit_cap = outcome.hit_cap
    await runs.finish(
        session,
        run_id,
        state=AgentRunState.FAILED if hit_cap else AgentRunState.SUCCEEDED,
        reason=AgentRunReason.ITERATION_CAP if hit_cap else None,
        output=outcome.output or None,
        tokens_used=tokens,
        error=f"iteration cap reached after {outcome.iterations} rounds" if hit_cap else None,
    )
    if tokens:
        await record_usage(
            session,
            model_pk=model_pk,
            function=AiFunction.AGENT_ENGINE,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )
    # Unconditional: record_usage bails without committing when the AiModel row
    # vanished mid-run — the terminal write must survive regardless.
    await session.commit()
