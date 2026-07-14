"""run_agent end-to-end: LLM on respx, journal + spend bucket asserted (P0)."""

from typing import cast

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine import jobs, runs
from achilles.agent_engine.constants import AgentRunReason, AgentRunState, AgentRunTrigger
from achilles.agent_engine.models import AgentRun
from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AiModel, ModelUsage
from achilles.config import Settings
from achilles.knowledge_store.models import PlatformSettings
from tests.ai_foundation.unit.llm_wire import openai_chunk, openai_sse, openai_text_body
from tests.factories.agents import allow_agent_model, create_agent
from tests.factories.ai import create_model, create_provider
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]

CTX = cast("Context", {})
LLM_BASE = "http://llm.test"
LLM_URL = f"{LLM_BASE}/v1/chat/completions"
USAGE = {"prompt_tokens": 100, "completion_tokens": 20}


@pytest.fixture(autouse=True)
def job_uses_test_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


async def _queued_agent(session: AsyncSession) -> tuple[int, int]:
    """(run_id, model_pk) — plain ids: the job and expire_all outlive the ORM rows."""
    user = await create_user(session)
    provider = await create_provider(
        session, adapter="openai_compatible", kind="local", base_url=LLM_BASE
    )
    model = await create_model(session, provider_id=provider.id, model_type="chat")
    allowed = await allow_agent_model(session, model.id)
    agent = await create_agent(session, user_id=user.id, model_id=allowed.id)
    run_id = await runs.start_run(session, agent_id=agent.id, trigger=str(AgentRunTrigger.MANUAL))
    await session.commit()
    return run_id, model.id


async def test_successful_run_journals_output_and_spend(
    db_session: AsyncSession, hibp_clean: respx.MockRouter
) -> None:
    run_id, model_pk = await _queued_agent(db_session)
    hibp_clean.post(LLM_URL).mock(
        return_value=Response(
            200,
            content=openai_text_body("Weekly digest ready.", usage=USAGE),
            headers={"content-type": "text/event-stream"},
        )
    )

    await jobs.run_agent(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.SUCCEEDED)
    assert run.output == "Weekly digest ready."
    assert run.tokens_used == 120
    assert run.started_at is not None
    assert run.finished_at is not None
    usage_row = await db_session.scalar(
        sa.select(ModelUsage).where(ModelUsage.function == str(AiFunction.AGENT_ENGINE))
    )
    assert usage_row is not None
    assert usage_row.model_id == model_pk
    assert usage_row.input_tokens == 100
    assert usage_row.output_tokens == 20


async def test_iteration_cap_fails_the_run_but_keeps_usage(
    db_session: AsyncSession, hibp_clean: respx.MockRouter
) -> None:
    run_id, _model_pk = await _queued_agent(db_session)
    await db_session.execute(
        sa.update(PlatformSettings).where(PlatformSettings.id == 1).values(agent_iteration_cap=1)
    )
    await db_session.commit()
    # The model insists on a tool call — the cap cuts the loop after round 1.
    tool_call_body = openai_sse(
        openai_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "ghost", "arguments": "{}"},
                    }
                ]
            }
        ),
        openai_chunk(finish="tool_calls"),
        openai_chunk(usage=USAGE),
    )
    hibp_clean.post(LLM_URL).mock(
        return_value=Response(
            200, content=tool_call_body, headers={"content-type": "text/event-stream"}
        )
    )

    await jobs.run_agent(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.FAILED)
    assert run.reason == str(AgentRunReason.ITERATION_CAP)
    assert run.tokens_used == 120  # spent tokens are journaled even on failure


async def test_unavailable_model_fails_the_run(db_session: AsyncSession) -> None:
    run_id, model_pk = await _queued_agent(db_session)
    await db_session.execute(
        sa.update(AiModel).where(AiModel.id == model_pk).values(is_enabled=False)
    )
    await db_session.commit()

    await jobs.run_agent(CTX, run_id=run_id)

    db_session.expire_all()
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.FAILED)
    assert run.reason == str(AgentRunReason.ERROR)
    assert run.error == "agent model is not available"
