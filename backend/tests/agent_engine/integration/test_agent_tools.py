"""Tool-belt assembly: emptiness gate + the agents_allowed filter (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.runtime.tools import build_agent_tools
from achilles.ai_foundation.models import Tool
from achilles.ai_foundation.tools.registry import discover_tool_types
from achilles.auth.security.crypto import derive_crypto_key
from achilles.config import Settings
from tests.factories.agents import add_agent_tool, create_agent
from tests.factories.knowledge import create_chunk, create_entity, create_source
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]


@pytest.fixture(autouse=True)
def _tool_types() -> None:
    """The registry is populated by app startup; these tests run without the app."""
    discover_tool_types()


def _key(test_settings: Settings) -> bytes:
    return derive_crypto_key(
        crypto_key=test_settings.crypto_key, secret_key=test_settings.secret_key
    )


async def _agent(session: AsyncSession) -> tuple[int, int]:
    user = await create_user(session)
    agent = await create_agent(session, user_id=user.id)
    return agent.id, user.id


async def _fill_store(session: AsyncSession) -> None:
    source = await create_source(session)
    entity = await create_entity(session, source_id=source.id)
    await create_chunk(session, entity_id=entity.id)


async def test_empty_store_offers_no_ks_core(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    agent_id, user_id = await _agent(db_session)
    tools = await build_agent_tools(
        db_session, crypto_key=_key(test_settings), agent_id=agent_id, user_id=user_id
    )
    assert tools == []  # hub mode: no core, no optional picks either


async def test_filled_store_offers_the_locked_core(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    agent_id, user_id = await _agent(db_session)
    await _fill_store(db_session)
    tools = await build_agent_tools(
        db_session, crypto_key=_key(test_settings), agent_id=agent_id, user_id=user_id
    )
    assert [tool.spec.name for tool in tools] == ["search", "graph", "sql"]


async def test_optional_tool_needs_both_the_pick_and_the_admin_flag(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    agent_id, user_id = await _agent(db_session)
    key = _key(test_settings)
    fetch_url = (
        await db_session.execute(sa.select(Tool).where(Tool.name == "fetch_url"))
    ).scalar_one()
    await add_agent_tool(db_session, agent_id=agent_id, tool_id=fetch_url.id)

    # Picked by the owner but not allowed by the admin → absent.
    names = [
        t.spec.name
        for t in await build_agent_tools(
            db_session, crypto_key=key, agent_id=agent_id, user_id=user_id
        )
    ]
    assert "fetch_url" not in names

    fetch_url.agents_allowed = True
    await db_session.commit()
    names = [
        t.spec.name
        for t in await build_agent_tools(
            db_session, crypto_key=key, agent_id=agent_id, user_id=user_id
        )
    ]
    assert "fetch_url" in names

    # Allowed but not picked by this agent → absent for another agent.
    other_user = await create_user(db_session)
    other = await create_agent(db_session, user_id=other_user.id)
    names = [
        t.spec.name
        for t in await build_agent_tools(
            db_session, crypto_key=key, agent_id=other.id, user_id=other_user.id
        )
    ]
    assert "fetch_url" not in names
