"""KS core handlers: owner identity pass-through, data-shaped results (P0)."""

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.runtime import tools as tools_mod
from achilles.agent_engine.runtime.tools import GRAPH_MAX_DEPTH, KnowledgeCore
from achilles.knowledge_store.retrieval.evidence import Evidence
from achilles.knowledge_store.retrieval.fusion import FusedHit
from achilles.knowledge_store.retrieval.hits import Hit
from achilles.knowledge_store.retrieval.hybrid import HybridResult
from achilles.knowledge_store.retrieval.sql import parse_filters

pytestmark = [pytest.mark.unit, pytest.mark.p0]

OWNER_ID = 42


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    """Only what KnowledgeCore.graph needs after the primitive: the title query."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []

    async def execute(self, stmt: object) -> _FakeResult:
        del stmt
        return _FakeResult(self.rows)


def _session(rows: list[tuple[Any, ...]] | None = None) -> AsyncSession:
    return cast("AsyncSession", _FakeSession(rows))


async def test_search_calls_hybrid_under_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_search(session: object, **kwargs: object) -> HybridResult:
        seen.update(kwargs)
        return HybridResult(
            hits=[FusedHit(entity_id=7, score=1.0, best_chunk_id=None)],
            degraded=False,
            query_vector=None,
            embedding_model=None,
        )

    async def fake_evidence(session: object, hits: object) -> list[Evidence]:
        del session, hits
        return [
            Evidence(
                entity_id=7,
                title="Quarterly report",
                url="http://doc",
                source_type="page",
                best_chunk_id=None,
                best_chunk_text="Revenue grew.",
            )
        ]

    monkeypatch.setattr(tools_mod.hybrid, "search", fake_search)
    monkeypatch.setattr(tools_mod, "fetch_evidence", fake_evidence)
    core = KnowledgeCore(_session(), user_id=OWNER_ID)

    result = await core.search(query="  quarterly   report ")

    assert seen["user_id"] == OWNER_ID  # identity from context, never an argument
    assert seen["query"] == "quarterly report"  # normalized standalone query
    assert "entity_id=7" in result  # graph-chainable ids in the output
    assert "Revenue grew." in result


async def test_search_requires_a_query() -> None:
    core = KnowledgeCore(_session(), user_id=OWNER_ID)
    assert "Error" in await core.search(query="   ")


async def test_graph_walks_from_ids_with_bounded_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def fake_graph(session: object, **kwargs: object) -> list[Hit]:
        seen.update(kwargs)
        return [Hit(entity_id=9, score=0.5, depth=1)]

    monkeypatch.setattr(tools_mod.graph, "search", fake_graph)
    core = KnowledgeCore(_session(rows=[(9, "Linked doc", "page")]), user_id=OWNER_ID)

    result = await core.graph(entity_ids=[7], depth=99)

    assert seen["user_id"] == OWNER_ID
    assert seen["start_ids"] == [7]
    assert seen["depth"] == GRAPH_MAX_DEPTH  # 99 clamped to the module cap
    assert "entity_id=9" in result
    assert "Linked doc" in result


async def test_graph_requires_entity_ids() -> None:
    core = KnowledgeCore(_session(), user_id=OWNER_ID)
    assert "Error" in await core.graph(entity_ids=[])


async def test_sql_aggregates_under_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_aggregate(session: object, **kwargs: object) -> list[tuple[str, int]]:
        seen.update(kwargs)
        return [("page", 12), ("ticket", 3)]

    monkeypatch.setattr(tools_mod.aggregate, "aggregate", fake_aggregate)
    core = KnowledgeCore(_session(), user_id=OWNER_ID)

    result = await core.sql(group_by="source_type")

    assert seen["user_id"] == OWNER_ID
    assert result == "page: 12\nticket: 3"


async def test_sql_unknown_axis_is_an_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_aggregate(session: object, **kwargs: object) -> list[tuple[str, int]]:
        raise ValueError("unknown group_by 'evil'")

    monkeypatch.setattr(tools_mod.aggregate, "aggregate", fake_aggregate)
    core = KnowledgeCore(_session(), user_id=OWNER_ID)

    assert "Error" in await core.sql(group_by="evil")


def test_parse_filters_ignores_junk() -> None:
    assert parse_filters("not a dict") is None
    assert parse_filters({"unknown": 1}) is None
    parsed = parse_filters({"source_types": ["page"], "updated_from": "2026-01-01T00:00:00Z"})
    assert parsed is not None
    assert parsed.source_types == ["page"]
    assert parsed.source_updated_from is not None
