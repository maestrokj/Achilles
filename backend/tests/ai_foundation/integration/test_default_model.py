"""Exactly one default per list — partial UNIQUE WHERE is_default (tests.html, P1)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.models import AgentModel, ChatModel
from tests.factories.ai import create_model

pytestmark = [pytest.mark.integration, pytest.mark.p1]


@pytest.mark.parametrize("list_model", [ChatModel, AgentModel])
async def test_second_default_bounces(
    db_session: AsyncSession, list_model: type[ChatModel | AgentModel]
) -> None:
    first = await create_model(db_session)
    second = await create_model(db_session)
    db_session.add(list_model(model_id=first.id, is_default=True))
    await db_session.commit()

    db_session.add(list_model(model_id=second.id, is_default=True))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize("list_model", [ChatModel, AgentModel])
async def test_default_swap_is_atomic(
    db_session: AsyncSession, list_model: type[ChatModel | AgentModel]
) -> None:
    first = await create_model(db_session)
    second = await create_model(db_session)
    db_session.add(list_model(model_id=first.id, is_default=True))
    db_session.add(list_model(model_id=second.id))
    await db_session.commit()

    # One transaction: drop the old flag and raise the new one together.
    await db_session.execute(
        sa.update(list_model).where(list_model.model_id == first.id).values(is_default=False)
    )
    await db_session.execute(
        sa.update(list_model).where(list_model.model_id == second.id).values(is_default=True)
    )
    await db_session.commit()

    defaults = (
        (await db_session.execute(sa.select(list_model).where(list_model.is_default)))
        .scalars()
        .all()
    )
    assert [row.model_id for row in defaults] == [second.id]


@pytest.mark.parametrize("list_model", [ChatModel, AgentModel])
async def test_same_model_listed_once(
    db_session: AsyncSession, list_model: type[ChatModel | AgentModel]
) -> None:
    model = await create_model(db_session)
    db_session.add(list_model(model_id=model.id))
    await db_session.commit()

    db_session.add(list_model(model_id=model.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()
