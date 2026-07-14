"""AI spend read side: totals, per-user attribution, per-model money (tests.html)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from achilles.agent_engine.constants import WEEK_RESET_WEEKDAY
from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AiModel
from achilles.query_engine.constants import MessageRole, Surface
from achilles.query_engine.models import Conversation, Message
from tests.auth.integration.conftest import AuthorizeFn
from tests.conftest import FlushRedis
from tests.factories.admin import set_platform_settings
from tests.factories.agents import allow_agent_model, create_agent, create_run
from tests.factories.ai import create_usage, reset_ai_catalog
from tests.factories.knowledge import create_source
from tests.factories.users import create_user
from tests.knowledge_store.conftest import KS_TABLES

pytestmark = [pytest.mark.integration, pytest.mark.p1]

URL = "/api/v1/admin/usage"

# Spend tests touch agents, dialogues and the aggregate — compose the full set.
_TABLES = (
    "agent_runs",
    "agent_tools",
    "agents",
    "retrieval_trace",
    "messages",
    "conversations",
    "chat_models",
    "agent_models",
    *KS_TABLES,
)


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    """Overrides the package conftest's clean_state with the wider table set."""
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await reset_ai_catalog(conn)
    await flush_redis()


async def _chat_spend(
    session: AsyncSession, *, user_id: int, tokens: int, model: str = "gpt-4o", ago_days: int = 0
) -> None:
    conversation = Conversation(user_id=user_id, surface=str(Surface.WEB))
    session.add(conversation)
    await session.flush()
    moment = datetime.now(UTC) - timedelta(days=ago_days)
    session.add(
        Message(
            conversation_id=conversation.id,
            role=str(MessageRole.ASSISTANT),
            content="answer",
            model=model,
            tokens_used=tokens,
            created_at=moment,
        )
    )
    await session.commit()


async def _agent_spend(
    session: AsyncSession, *, user_id: int, tokens: int, name: str = "Digest"
) -> int:
    allowed = await allow_agent_model(session)
    agent = await create_agent(session, user_id=user_id, model_id=allowed.id, name=name)
    await create_run(session, agent_id=agent.id, tokens_used=tokens)
    return agent.id


async def test_per_user_attribution_and_sort(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    heavy = await create_user(db_session, full_name="Heavy Spender")
    light = await create_user(db_session, full_name="Light Spender")
    await _agent_spend(db_session, user_id=heavy.id, tokens=1_200_000)
    await _chat_spend(db_session, user_id=heavy.id, tokens=340_000)
    await _chat_spend(db_session, user_id=light.id, tokens=120_000)
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    rows = body["by_user"]["items"]
    assert rows[0]["full_name"] == "Heavy Spender"
    assert rows[0]["agent_tokens"] == 1_200_000
    assert rows[0]["chat_tokens"] == 340_000
    assert rows[0]["total_tokens"] == 1_540_000
    assert rows[1]["full_name"] == "Light Spender"

    searched = (await client.get(URL, params={"q": "Light"})).json()
    assert [r["full_name"] for r in searched["by_user"]["items"]] == ["Light Spender"]


async def test_company_total_is_wider_than_people(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    """Indexing is a system spend: visible in the totals and by-model, absent per-user."""
    admin = await create_user(db_session, role="admin")
    source = await create_source(db_session)
    del source
    model = await _seeded_model_id(db_session)
    await create_usage(
        db_session,
        model_id=model,
        function=AiFunction.HARVESTER_EMBEDDING,
        bucket_date=datetime.now(UTC).date(),
        input_tokens=500_000,
        request_count=10,
    )
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    assert body["totals"]["week"]["tokens"] == 500_000
    assert all(row["total_tokens"] == 0 for row in body["by_user"]["items"])
    functions = {m["function"] for m in body["by_model"]}
    assert "harvester_embedding" in functions


async def test_week_boundary_respects_org_window(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, full_name="Boundary Person")
    # The last day of the previous week: outside the current reset window on
    # any run date (a fixed ago_days=8 broke on Sundays), inside prev_week.
    days_into_week = (datetime.now(UTC).weekday() - WEEK_RESET_WEEKDAY) % 7
    await _chat_spend(db_session, user_id=person.id, tokens=90_000, ago_days=days_into_week + 1)
    await authorize(admin.email)

    current = (await client.get(URL, params={"q": "Boundary"})).json()
    assert current["by_user"]["items"][0]["chat_tokens"] == 0

    prev = (await client.get(URL, params={"q": "Boundary", "window": "prev_week"})).json()
    assert prev["by_user"]["items"][0]["chat_tokens"] == 90_000, (
        "an out-of-window spend stays visible in the earlier window"
    )


async def test_over_limit_pills(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, full_name="Capped Person")
    await _agent_spend(db_session, user_id=person.id, tokens=2_000_000)
    await set_platform_settings(
        db_session, agent_weekly_token_budget=2_000_000, chat_weekly_token_budget=500_000
    )
    await authorize(admin.email)

    body = (await client.get(URL, params={"q": "Capped"})).json()
    row = body["by_user"]["items"][0]
    assert row["agent_over_limit"] is True
    assert row["chat_over_limit"] is False
    assert body["limits"]["agent_weekly_token_budget"] == 2_000_000


async def test_user_detail_breaks_down_agents_and_chat(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    person = await create_user(db_session, full_name="Detailed Person")
    agent_id = await _agent_spend(
        db_session, user_id=person.id, tokens=820_000, name="Release digest"
    )
    await _chat_spend(db_session, user_id=person.id, tokens=250_000, model="gpt-4o")
    await _chat_spend(db_session, user_id=person.id, tokens=90_000, model="claude-sonnet")
    await authorize(admin.email)

    body = (await client.get(f"{URL}/{person.id}")).json()
    assert body["agent_tokens"] == 820_000
    assert body["chat_tokens"] == 340_000
    assert body["agents"][0]["agent_id"] == agent_id  # the row deep-links to the agent
    assert body["agents"][0]["name"] == "Release digest"
    assert body["agents"][0]["runs"] == 1
    chat_models = {row["model"]: row["tokens"] for row in body["chat"]}
    assert chat_models == {"gpt-4o": 250_000, "claude-sonnet": 90_000}


async def test_by_model_money_and_null_poisoning(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    admin = await create_user(db_session, role="admin")
    model = await _seeded_model_id(db_session)
    today = datetime.now(UTC).date()
    await create_usage(
        db_session,
        model_id=model,
        function=AiFunction.CHAT,
        bucket_date=today,
        input_tokens=1000,
        output_tokens=200,
        cost=Decimal("1.50"),
    )
    await create_usage(
        db_session,
        model_id=model,
        function=AiFunction.QUERY_RAG,
        bucket_date=today,
        input_tokens=400,
        cost=None,  # prices were not set — the bucket is poisoned
    )
    await authorize(admin.email)

    body = (await client.get(URL)).json()
    by_function = {m["function"]: m for m in body["by_model"]}
    assert Decimal(by_function["chat"]["cost"]) == Decimal("1.50")
    assert by_function["query_rag"]["cost"] is None, "an unpriced bucket answers honest null"
    assert body["totals"]["week"]["cost"] is None, "one unpriced bucket poisons the total"


async def test_member_is_403(client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn):
    member = await create_user(db_session)
    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403
    assert (await client.get(f"{URL}/{member.id}")).status_code == 403


async def _seeded_model_id(session: AsyncSession) -> int:
    model_id = await session.scalar(sa.select(AiModel.id).order_by(AiModel.id).limit(1))
    assert model_id is not None, "the migration seeds builtin models"
    return model_id
