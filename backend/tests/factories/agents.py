"""Agent Engine factory helpers: agents, journal rows, the model allow-list."""

import itertools
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.constants import AgentRunState, AgentRunTrigger
from achilles.agent_engine.models import Agent, AgentRun, AgentTool
from achilles.ai_foundation.models import AgentModel
from achilles.knowledge_store.models import PlatformSettings
from tests.factories.ai import create_model

_seq = itertools.count(1)


async def create_agent(session: AsyncSession, *, user_id: int, **kwargs: object) -> Agent:
    n = next(_seq)
    agent = Agent(
        **{
            "user_id": user_id,
            "name": f"Agent {n}",
            "prompt": f"Weekly digest {n}",
            **kwargs,
        }  # type: ignore[arg-type]
    )
    session.add(agent)
    await session.commit()
    return agent


async def create_run(
    session: AsyncSession,
    *,
    agent_id: int,
    state: AgentRunState = AgentRunState.SUCCEEDED,
    tokens_used: int = 0,
    finished_ago: timedelta | None = timedelta(minutes=5),
    **kwargs: object,
) -> AgentRun:
    finished_at = datetime.now(UTC) - finished_ago if finished_ago is not None else None
    started_at = finished_at - timedelta(minutes=1) if finished_at is not None else None
    run = AgentRun(
        **{
            "agent_id": agent_id,
            "trigger": str(AgentRunTrigger.MANUAL),
            "state": str(state),
            "tokens_used": tokens_used,
            "started_at": started_at,
            "finished_at": finished_at,
            **kwargs,
        }  # type: ignore[arg-type]
    )
    session.add(run)
    await session.commit()
    return run


async def allow_agent_model(
    session: AsyncSession, model_pk: int | None = None, *, default: bool = True
) -> AgentModel:
    """Put a catalog model on the agent allow-list (data-model.html#t-agent-models)."""
    if model_pk is None:
        model_pk = (await create_model(session)).id
    row = AgentModel(model_id=model_pk, is_default=default)
    session.add(row)
    await session.commit()
    return row


async def add_agent_tool(session: AsyncSession, *, agent_id: int, tool_id: int) -> AgentTool:
    row = AgentTool(agent_id=agent_id, tool_id=tool_id)
    session.add(row)
    await session.commit()
    return row


async def set_agent_budget(session: AsyncSession, limit: int | None) -> None:
    await session.execute(
        sa.update(PlatformSettings)
        .where(PlatformSettings.id == 1)
        .values(agent_weekly_token_budget=limit)
    )
    await session.commit()
