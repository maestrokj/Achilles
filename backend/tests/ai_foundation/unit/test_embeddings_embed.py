"""embed(): the OpenAI-compatible call behind every vector consumer (unit).

Same contract as the rest of embeddings_client — best-effort, never raises;
None is the degradation signal the callers branch on.
"""

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.services import embeddings_client
from tests.factories.ai import assign_embedding

pytestmark = [pytest.mark.unit, pytest.mark.p1]

# The conftest egress guard owns this host with a url__startswith route; re-declaring
# the same pattern replaces its response instead of racing it for interception order.
PATTERN = {"url__startswith": "http://embeddings"}


async def test_no_assignment_is_none_without_egress(
    db_session: AsyncSession, embeddings_runtime_mock: respx.MockRouter
):
    assert await embeddings_client.embed(db_session, ["text"]) is None
    assert [
        c for c in embeddings_runtime_mock.calls if c.request.url.path.endswith("embeddings")
    ] == []


async def test_vectors_come_back_in_input_order(
    db_session: AsyncSession, embeddings_runtime_mock: respx.MockRouter
):
    await assign_embedding(db_session)
    embeddings_runtime_mock.post(**PATTERN).mock(
        return_value=httpx.Response(
            200,
            json={
                # Deliberately shuffled: the client must sort by index.
                "data": [
                    {"index": 1, "embedding": [0.2]},
                    {"index": 0, "embedding": [0.1]},
                ],
                "usage": {"prompt_tokens": 9},
            },
        )
    )

    result = await embeddings_client.embed(db_session, ["a", "b"])

    assert result is not None
    assert result.vectors == [[0.1], [0.2]]
    assert result.prompt_tokens == 9
    assert result.model.model_id == "BAAI/bge-m3"


async def test_missing_usage_is_none_tokens_not_a_crash(
    db_session: AsyncSession, embeddings_runtime_mock: respx.MockRouter
):
    await assign_embedding(db_session)
    embeddings_runtime_mock.post(**PATTERN).mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})
    )

    result = await embeddings_client.embed(db_session, ["a"])

    assert result is not None
    assert result.prompt_tokens is None


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503), httpx.Response(500)],
    ids=["loading", "error"],
)
async def test_runtime_failures_degrade_to_none(
    db_session: AsyncSession, embeddings_runtime_mock: respx.MockRouter, failure: httpx.Response
):
    await assign_embedding(db_session)
    embeddings_runtime_mock.post(**PATTERN).mock(return_value=failure)
    assert await embeddings_client.embed(db_session, ["a"]) is None


async def test_connect_error_degrades_to_none(
    db_session: AsyncSession, embeddings_runtime_mock: respx.MockRouter
):
    await assign_embedding(db_session)
    embeddings_runtime_mock.post(**PATTERN).mock(side_effect=httpx.ConnectError("down"))
    assert await embeddings_client.embed(db_session, ["a"]) is None
