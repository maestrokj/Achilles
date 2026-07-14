"""model_usage bucket semantics: UNIQUE upsert target, SET NULL survival (tests.html, P1).

The recorder service's upsert math is covered in test_usage_recorder.py; this
file pins the DB shape it relies on.
"""

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AiModel, ModelUsage
from tests.factories.ai import create_model, create_usage

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BUCKET = date(2026, 7, 1)


async def test_same_bucket_is_unique(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    await create_usage(db_session, model_id=model.id, function=AiFunction.CHAT, bucket_date=BUCKET)

    db_session.add(ModelUsage(model_id=model.id, function=AiFunction.CHAT, bucket_date=BUCKET))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_other_function_and_date_get_own_rows(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    await create_usage(db_session, model_id=model.id, function=AiFunction.CHAT, bucket_date=BUCKET)
    await create_usage(
        db_session, model_id=model.id, function=AiFunction.QUERY_RAG, bucket_date=BUCKET
    )
    await create_usage(
        db_session, model_id=model.id, function=AiFunction.CHAT, bucket_date=date(2026, 7, 2)
    )

    count = (
        await db_session.execute(sa.select(sa.func.count()).select_from(ModelUsage))
    ).scalar_one()
    assert count == 3


async def test_usage_survives_model_deletion(db_session: AsyncSession) -> None:
    model = await create_model(db_session)
    usage = await create_usage(
        db_session, model_id=model.id, function=AiFunction.CHAT, input_tokens=100
    )
    usage_id = usage.id

    await db_session.execute(sa.delete(AiModel).where(AiModel.id == model.id))
    await db_session.commit()
    db_session.expire_all()

    survivor = (
        await db_session.execute(sa.select(ModelUsage).where(ModelUsage.id == usage_id))
    ).scalar_one()
    assert survivor.model_id is None
    assert survivor.input_tokens == 100
