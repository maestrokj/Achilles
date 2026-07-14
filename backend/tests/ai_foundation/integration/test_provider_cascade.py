"""Provider/catalog constraints: cascade, CHECKs, function dictionaries (tests.html, P1)."""

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AiModel, AiProvider, ModelAssignment, ModelUsage
from tests.factories.ai import create_model, create_provider

pytestmark = [pytest.mark.integration, pytest.mark.p1]


async def test_provider_delete_cascades_models(db_session: AsyncSession) -> None:
    provider = await create_provider(db_session)
    model = await create_model(db_session, provider_id=provider.id)

    await db_session.execute(sa.delete(AiProvider).where(AiProvider.id == provider.id))
    await db_session.commit()

    assert (
        await db_session.execute(sa.select(AiModel).where(AiModel.id == model.id))
    ).scalar_one_or_none() is None


async def test_local_provider_requires_base_url(db_session: AsyncSession) -> None:
    db_session.add(AiProvider(name="Local", kind="local", adapter="ollama", base_url=None))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_duplicate_model_id_bounces(db_session: AsyncSession) -> None:
    provider = await create_provider(db_session)
    await create_model(db_session, provider_id=provider.id, model_id="gpt-4o")

    db_session.add(
        AiModel(
            provider_id=provider.id,
            model_id="gpt-4o",
            display_name="Duplicate",
            model_type="chat",
            origin="manual",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize("function", [AiFunction.CHAT, AiFunction.AGENT_ENGINE])
async def test_assignment_rejects_non_system_functions(
    db_session: AsyncSession, function: AiFunction
) -> None:
    model = await create_model(db_session)
    db_session.add(ModelAssignment(function=function, model_id=model.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.parametrize("function", list(AiFunction))
async def test_usage_accepts_full_dictionary(
    db_session: AsyncSession, function: AiFunction
) -> None:
    model = await create_model(db_session)
    db_session.add(ModelUsage(model_id=model.id, function=function, bucket_date=date(2026, 7, 1)))
    await db_session.commit()
