"""record_usage upsert math: increments, cost accumulation, NULL poison (P1)."""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import ModelUsage
from achilles.ai_foundation.services.usage import record_usage
from tests.factories.ai import create_model

pytestmark = [pytest.mark.integration, pytest.mark.p1]

BUCKET = date(2026, 7, 1)


async def _bucket_row(session: AsyncSession) -> ModelUsage:
    session.expire_all()
    return (await session.execute(sa.select(ModelUsage))).scalar_one()


async def test_same_bucket_increments(db_session: AsyncSession):
    model = await create_model(
        db_session, price_input=Decimal("2.00"), price_output=Decimal("10.00")
    )
    for _ in range(2):
        await record_usage(
            db_session,
            model_pk=model.id,
            function=AiFunction.CHAT,
            input_tokens=1_000_000,
            output_tokens=500_000,
            on=BUCKET,
        )

    row = await _bucket_row(db_session)
    assert row.request_count == 2
    assert row.input_tokens == 2_000_000
    assert row.output_tokens == 1_000_000
    # 2 x (1M * $2/1M + 0.5M * $10/1M) = 2 x $7
    assert row.cost == Decimal("14.00")


async def test_unpriced_model_costs_null(db_session: AsyncSession):
    model = await create_model(db_session)  # no prices
    await record_usage(
        db_session, model_pk=model.id, function=AiFunction.CHAT, input_tokens=100, on=BUCKET
    )
    assert (await _bucket_row(db_session)).cost is None


async def test_price_gap_poisons_cost_forever(db_session: AsyncSession):
    model = await create_model(
        db_session, price_input=Decimal("2.00"), price_output=Decimal("10.00")
    )
    await record_usage(
        db_session,
        model_pk=model.id,
        function=AiFunction.CHAT,
        input_tokens=1_000_000,
        on=BUCKET,
    )
    assert (await _bucket_row(db_session)).cost == Decimal("2.00")

    # A side with tokens but no price → unknown delta → the bucket goes NULL…
    model.price_output = None
    await db_session.commit()
    await record_usage(
        db_session,
        model_pk=model.id,
        function=AiFunction.CHAT,
        input_tokens=0,
        output_tokens=10,
        on=BUCKET,
    )
    assert (await _bucket_row(db_session)).cost is None

    # …and stays NULL even when later deltas are priced again.
    model.price_output = Decimal("10.00")
    await db_session.commit()
    await record_usage(
        db_session,
        model_pk=model.id,
        function=AiFunction.CHAT,
        input_tokens=1_000_000,
        on=BUCKET,
    )
    row = await _bucket_row(db_session)
    assert row.cost is None
    assert row.input_tokens == 2_000_000  # counters keep counting


async def test_zero_output_side_needs_no_price(db_session: AsyncSession):
    model = await create_model(
        db_session, model_type="embedding", price_input=Decimal("0.10")
    )  # embeddings: no output price at all
    await record_usage(
        db_session,
        model_pk=model.id,
        function=AiFunction.HARVESTER_EMBEDDING,
        input_tokens=1_000_000,
        on=BUCKET,
    )
    assert (await _bucket_row(db_session)).cost == Decimal("0.10")


async def test_unknown_model_is_dropped(db_session: AsyncSession):
    await record_usage(
        db_session, model_pk=99_999, function=AiFunction.CHAT, input_tokens=5, on=BUCKET
    )
    assert (
        await db_session.execute(sa.select(sa.func.count()).select_from(ModelUsage))
    ).scalar_one() == 0
