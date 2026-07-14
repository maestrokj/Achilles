"""A model in use cannot be deleted — the lock lives in the DB (tests.html, P0).

RESTRICT on model_assignments / chat_models / agent_models must bounce even a
raw DELETE that bypasses the service layer.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AgentModel, AiModel, ChatModel, ModelAssignment
from tests.factories.ai import create_model

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def _raw_delete(session: AsyncSession, model_id: int) -> None:
    await session.execute(sa.delete(AiModel).where(AiModel.id == model_id))
    await session.commit()


async def test_assigned_model_delete_bounces(db_session: AsyncSession) -> None:
    model = await create_model(db_session, model_type="embedding")
    db_session.add(ModelAssignment(function=AiFunction.HARVESTER_EMBEDDING, model_id=model.id))
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await _raw_delete(db_session, model.id)


async def test_chat_listed_model_delete_bounces(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    db_session.add(ChatModel(model_id=model.id, is_default=True))
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await _raw_delete(db_session, model.id)


async def test_agent_listed_model_delete_bounces(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    db_session.add(AgentModel(model_id=model.id))
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await _raw_delete(db_session, model.id)


async def test_free_model_deletes(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    await _raw_delete(db_session, model.id)
    assert (
        await db_session.execute(sa.select(AiModel).where(AiModel.id == model.id))
    ).scalar_one_or_none() is None
