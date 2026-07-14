"""is_empty derivation: presence of live fragments, never a stored flag (unit).

The real query runs in integration (test_soft_delete flips it); here the
derivation contract is pinned against a stubbed session.
"""

from typing import Any

import pytest

from achilles.knowledge_store.models import Chunk
from achilles.knowledge_store.services.emptiness import is_empty

pytestmark = [pytest.mark.unit]


class _StubSession:
    def __init__(self, has_chunks: bool) -> None:
        self._has_chunks = has_chunks
        self.statements: list[Any] = []

    async def scalar(self, stmt: Any) -> bool:
        self.statements.append(stmt)
        return self._has_chunks


async def test_no_live_chunks_means_empty():
    assert await is_empty(_StubSession(has_chunks=False)) is True  # type: ignore[arg-type]


async def test_first_chunk_flips_it():
    assert await is_empty(_StubSession(has_chunks=True)) is False  # type: ignore[arg-type]


async def test_derivation_excludes_soft_deleted_chunks():
    session = _StubSession(has_chunks=False)
    await is_empty(session)  # type: ignore[arg-type]
    assert "is_deleted" in str(session.statements[0])


def test_is_empty_is_derived_not_stored():
    assert not hasattr(Chunk, "is_empty")
    assert "is_empty" not in Chunk.__table__.columns
