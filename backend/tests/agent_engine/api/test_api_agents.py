"""Owner surface of /agents: CRUD, run gates, journal (P0, index.html#api)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.constants import AgentRunReason, AgentRunState
from achilles.agent_engine.models import AgentRun
from achilles.ai_foundation.models import AgentModel, Tool
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.agents import (
    allow_agent_model,
    create_agent,
    create_run,
    set_agent_budget,
)
from tests.factories.users import User, create_user

pytestmark = [pytest.mark.api, pytest.mark.p0]

AGENTS = "/api/v1/agents"


@pytest.fixture
async def member(db_session: AsyncSession, authorize: AuthorizeFn) -> User:
    user = await create_user(db_session)
    await authorize(user.email)
    return user


@pytest.fixture
async def allowed_model(db_session: AsyncSession) -> AgentModel:
    return await allow_agent_model(db_session)


async def test_create_presets_the_default_model(
    client: AsyncClient, member: User, allowed_model: AgentModel
) -> None:
    resp = await client.post(AGENTS, json={"name": "Digest", "prompt": "Summarize the week"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["model_id"] == allowed_model.id  # preset = the list default
    assert body["status"] == "active"
    assert body["enabled"] is True
    assert body["tool_ids"] == []
    assert body["schedule"] is None
    assert body["next_run_at"] is None  # manual-only


async def test_create_without_any_model_leaves_the_agent_standing(
    client: AsyncClient, member: User
) -> None:
    resp = await client.post(AGENTS, json={"name": "A", "prompt": "p"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["model_id"] is None
    assert body["status"] == "model_missing"


async def test_create_rejects_a_model_outside_the_list(
    client: AsyncClient, member: User, allowed_model: AgentModel
) -> None:
    resp = await client.post(AGENTS, json={"name": "A", "prompt": "p", "model_id": 99_999})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "schedule",
    [
        {"type": "cron", "expr": "* * * * *"},  # raw cron is v2
        {"type": "interval", "every_hours": 0},
        {"type": "interval", "every_hours": 25},
        {"type": "calendar", "cadence": "weekly", "time": "09:00"},  # weekday missing
        {"type": "calendar", "cadence": "daily", "time": "9 am"},
        {"type": "calendar", "cadence": "daily", "time": "25:00"},
    ],
)
async def test_create_rejects_malformed_schedules(
    client: AsyncClient, member: User, allowed_model: AgentModel, schedule: dict[str, object]
) -> None:
    resp = await client.post(AGENTS, json={"name": "A", "prompt": "p", "schedule": schedule})
    assert resp.status_code == 422


async def test_interval_schedule_sets_next_run(
    client: AsyncClient, member: User, allowed_model: AgentModel
) -> None:
    resp = await client.post(
        AGENTS,
        json={"name": "A", "prompt": "p", "schedule": {"type": "interval", "every_hours": 6}},
    )
    assert resp.status_code == 201
    assert resp.json()["next_run_at"] is not None


async def test_list_shows_own_agents_with_the_budget_envelope(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    stranger = await create_user(db_session)
    await create_agent(db_session, user_id=stranger.id)
    mine = await create_agent(db_session, user_id=member.id, model_id=allowed_model.id)
    await create_run(db_session, agent_id=mine.id, tokens_used=70)
    await set_agent_budget(db_session, 1_000)

    resp = await client.get(AGENTS)
    assert resp.status_code == 200
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [mine.id]
    assert body["budget"] == {
        "used": 70,
        "limit": 1000,
        "week_resets_at": body["budget"]["week_resets_at"],
    }
    last = body["items"][0]["last_run"]
    assert last["state"] == "succeeded"
    assert last["tokens_used"] == 70


async def test_foreign_agent_is_404(
    client: AsyncClient, db_session: AsyncSession, member: User
) -> None:
    stranger = await create_user(db_session)
    foreign = await create_agent(db_session, user_id=stranger.id)
    for method, url in (
        ("get", f"{AGENTS}/{foreign.id}"),
        ("patch", f"{AGENTS}/{foreign.id}"),
        ("delete", f"{AGENTS}/{foreign.id}"),
        ("post", f"{AGENTS}/{foreign.id}/run"),
        ("get", f"{AGENTS}/{foreign.id}/runs"),
    ):
        resp = await client.request(method, url, json={} if method == "patch" else None)
        assert resp.status_code == 404, (method, url, resp.status_code)


async def test_patch_updates_fields_and_tools(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    web_search = (
        await db_session.execute(sa.select(Tool).where(Tool.name == "web_search"))
    ).scalar_one()
    web_search.agents_allowed = True
    await db_session.commit()
    agent = await create_agent(db_session, user_id=member.id, model_id=allowed_model.id)

    resp = await client.patch(
        f"{AGENTS}/{agent.id}",
        json={"name": "Renamed", "enabled": False, "tool_ids": [web_search.id]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["status"] == "disabled"
    assert body["tool_ids"] == [web_search.id]

    # A tool the admin has not allowed for agents is rejected.
    fetch_url = (
        await db_session.execute(sa.select(Tool).where(Tool.name == "fetch_url"))
    ).scalar_one()
    resp = await client.patch(f"{AGENTS}/{agent.id}", json={"tool_ids": [fetch_url.id]})
    assert resp.status_code == 422


async def test_tool_disabled_after_selection_is_kept_and_shown_disabled(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    web_search = (
        await db_session.execute(sa.select(Tool).where(Tool.name == "web_search"))
    ).scalar_one()
    web_search.agents_allowed = True
    await db_session.commit()
    agent = await create_agent(db_session, user_id=member.id, model_id=allowed_model.id)
    resp = await client.patch(f"{AGENTS}/{agent.id}", json={"tool_ids": [web_search.id]})
    assert resp.status_code == 200, resp.text

    # The admin revokes the tool for agents after it was already selected.
    web_search.agents_allowed = False
    await db_session.commit()

    body = (await client.get(f"{AGENTS}/{agent.id}")).json()
    assert body["tool_ids"] == [web_search.id]  # kept on the agent
    assert body["disabled_tools"] == [{"id": web_search.id, "name": "web_search"}]

    # Re-saving the agent echoes the grandfathered id — it must not 422.
    resp = await client.patch(f"{AGENTS}/{agent.id}", json={"tool_ids": [web_search.id]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_ids"] == [web_search.id]


async def test_delete_removes_the_agent_and_journal(
    client: AsyncClient, db_session: AsyncSession, member: User
) -> None:
    agent = await create_agent(db_session, user_id=member.id)
    await create_run(db_session, agent_id=agent.id)

    resp = await client.delete(f"{AGENTS}/{agent.id}")
    assert resp.status_code == 204
    assert (await client.get(f"{AGENTS}/{agent.id}")).status_code == 404
    assert (await db_session.scalar(sa.select(sa.func.count()).select_from(AgentRun))) == 0


async def test_manual_run_queues_and_publishes(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_durable: Redis,
    member: User,
    allowed_model: AgentModel,
) -> None:
    agent_id = (await create_agent(db_session, user_id=member.id, model_id=allowed_model.id)).id

    resp = await client.post(f"{AGENTS}/{agent_id}/run")
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    run = await db_session.get_one(AgentRun, run_id)
    assert run.state == str(AgentRunState.QUEUED)
    assert run.trigger == "manual"
    assert await redis_durable.exists(f"dedup:job:agent:{run_id}")

    # A second run under the active one → 409 + a visible skipped row.
    resp = await client.post(f"{AGENTS}/{agent_id}/run")
    assert resp.status_code == 409
    assert resp.json()["code"] == "AGENT_RUN_ACTIVE"
    db_session.expire_all()
    skipped = await db_session.scalar(
        sa.select(AgentRun).where(
            AgentRun.agent_id == agent_id, AgentRun.state == str(AgentRunState.SKIPPED)
        )
    )
    assert skipped is not None
    assert skipped.reason == str(AgentRunReason.ALREADY_RUNNING)


async def test_manual_run_on_a_durably_stopped_agent_is_409_without_rows(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    agent = await create_agent(
        db_session, user_id=member.id, model_id=allowed_model.id, enabled=False
    )

    resp = await client.post(f"{AGENTS}/{agent.id}/run")
    assert resp.status_code == 409
    assert resp.json()["code"] == "AGENT_NOT_RUNNABLE"
    assert (
        await db_session.scalar(
            sa.select(sa.func.count()).select_from(AgentRun).where(AgentRun.agent_id == agent.id)
        )
    ) == 0


async def test_manual_run_over_budget_is_409_with_a_skipped_row(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    agent_id = (await create_agent(db_session, user_id=member.id, model_id=allowed_model.id)).id
    await create_run(db_session, agent_id=agent_id, tokens_used=100)
    await set_agent_budget(db_session, 100)

    resp = await client.post(f"{AGENTS}/{agent_id}/run")
    assert resp.status_code == 409
    assert resp.json()["code"] == "AGENT_BUDGET_EXCEEDED"
    db_session.expire_all()
    skipped = await db_session.scalar(
        sa.select(AgentRun).where(
            AgentRun.agent_id == agent_id, AgentRun.state == str(AgentRunState.SKIPPED)
        )
    )
    assert skipped is not None
    assert skipped.reason == str(AgentRunReason.BUDGET_EXCEEDED)


async def test_journal_distinguishes_skipped_from_failed(
    client: AsyncClient, db_session: AsyncSession, member: User
) -> None:
    agent = await create_agent(db_session, user_id=member.id)
    await create_run(
        db_session,
        agent_id=agent.id,
        state=AgentRunState.FAILED,
        reason=str(AgentRunReason.ITERATION_CAP),
        output="partial",
        error="iteration cap reached after 15 rounds",
    )
    await create_run(
        db_session,
        agent_id=agent.id,
        state=AgentRunState.SKIPPED,
        reason=str(AgentRunReason.BUDGET_EXCEEDED),
    )

    resp = await client.get(f"{AGENTS}/{agent.id}/runs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["state"] for item in items] == ["skipped", "failed"]  # newest first
    assert items[0]["reason"] == "budget_exceeded"
    assert items[1]["reason"] == "iteration_cap"
    assert items[1]["output"] == "partial"


async def test_journal_paginates_with_a_cursor(
    client: AsyncClient, db_session: AsyncSession, member: User
) -> None:
    agent = await create_agent(db_session, user_id=member.id)
    for _ in range(3):
        await create_run(db_session, agent_id=agent.id)

    first = await client.get(f"{AGENTS}/{agent.id}/runs", params={"limit": 2})
    assert first.status_code == 200
    page = first.json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]

    second = await client.get(
        f"{AGENTS}/{agent.id}/runs", params={"limit": 2, "cursor": page["next_cursor"]}
    )
    rest = second.json()
    assert len(rest["items"]) == 1
    assert rest["next_cursor"] is None


async def test_options_lists_models_and_agent_tools(
    client: AsyncClient, db_session: AsyncSession, member: User, allowed_model: AgentModel
) -> None:
    web_search = (
        await db_session.execute(sa.select(Tool).where(Tool.name == "web_search"))
    ).scalar_one()
    web_search.agents_allowed = True
    await db_session.commit()

    resp = await client.get(f"{AGENTS}/options")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["id"] for m in body["models"]] == [allowed_model.id]
    assert body["models"][0]["is_default"] is True
    assert [t["name"] for t in body["tools"]] == ["web_search"]
    assert body["core_tools"] == ["search", "graph", "sql"]
