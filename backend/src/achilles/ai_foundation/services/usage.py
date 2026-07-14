"""Spend recorder: the single write point of model_usage (cost-accounting.html).

Upsert by the (model · function · day) bucket — counters increment, cost
accumulates in dollars from the model's per-1M-token prices at write time.
A delta whose price is unknown poisons the bucket's cost to NULL: an honest
"prices not set" beats a silently understated total. Per-person attribution
is not here — it lives in the journals (messages/agent_runs tokens_used).

Query Engine writes here (chat turns, query_rag embeds), as do the KS
embed-on-write path (harvester_embedding) and Agent Engine run finalization
(agent_engine). The read side (GET /admin/usage) lives in usage_read.py.
"""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.models import AiModel, ModelUsage

logger = logging.getLogger(__name__)

_PRICE_UNIT = Decimal(1_000_000)  # prices are $ per 1M tokens


def _delta_cost(model: AiModel, input_tokens: int, output_tokens: int) -> Decimal | None:
    """Cost of this delta; None when a priced side is missing its price."""
    total = Decimal(0)
    for tokens, price in ((input_tokens, model.price_input), (output_tokens, model.price_output)):
        if tokens == 0:
            continue
        if price is None:
            return None
        total += Decimal(tokens) * price / _PRICE_UNIT
    return total


async def record_usage(
    session: AsyncSession,
    *,
    model_pk: int,
    function: AiFunction,
    input_tokens: int,
    output_tokens: int = 0,
    requests: int = 1,
    on: date | None = None,
) -> None:
    """Increment the daily bucket; concurrent writers meet at ON CONFLICT."""
    model = await session.get(AiModel, model_pk)
    if model is None:
        logger.warning("usage for unknown model %s dropped", model_pk)
        return
    bucket = on or datetime.now(UTC).date()
    delta = _delta_cost(model, input_tokens, output_tokens)

    statement = pg_insert(ModelUsage).values(
        model_id=model_pk,
        function=function.value,
        bucket_date=bucket,
        request_count=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=delta,
    )
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        constraint="uq_model_usage_bucket",
        set_={
            "request_count": ModelUsage.request_count + excluded.request_count,
            "input_tokens": ModelUsage.input_tokens + excluded.input_tokens,
            "output_tokens": ModelUsage.output_tokens + excluded.output_tokens,
            # Plain addition on purpose: NULL on either side poisons the sum,
            # so a once-unknown total never silently un-poisons or shrinks.
            "cost": ModelUsage.cost + excluded.cost,
        },
    )
    await session.execute(statement)
    await session.commit()
