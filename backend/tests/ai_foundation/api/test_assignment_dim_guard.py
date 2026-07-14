"""Assignment dimension guard: chunks.embedding halfvec(1024) is a schema fact (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.ai import create_model, create_provider, get_builtin_model

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


async def test_builtin_1024d_model_is_accepted(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    builtin = await get_builtin_model(db_session)
    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert resp.status_code == 200
    assert resp.json()["harvester_embedding"] == builtin.id


async def test_alien_dimension_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    provider = await create_provider(db_session)
    model = await create_model(
        db_session,
        provider_id=provider.id,
        model_type="embedding",
        meta={"embedding_dim": 1536},
    )

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": model.id})

    assert resp.status_code == 409
    assert resp.json()["code"] == "EMBEDDING_DIM_MISMATCH"


async def test_unreported_dimension_is_409_too(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    """A model that does not declare its dimension cannot fill the column."""
    provider = await create_provider(db_session)
    model = await create_model(db_session, provider_id=provider.id, model_type="embedding")

    resp = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": model.id})

    assert resp.status_code == 409
    assert resp.json()["code"] == "EMBEDDING_DIM_MISMATCH"
