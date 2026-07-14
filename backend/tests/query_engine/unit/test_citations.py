"""Citation resolution: only markers the answer used become citations (unit)."""

import pytest

from achilles.knowledge_store.retrieval.evidence import Evidence
from achilles.query_engine.rag.citations import resolve, used_markers
from achilles.query_engine.rag.search import PackedEvidence

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def packed(marker: int, entity_id: int) -> PackedEvidence:
    return PackedEvidence(
        marker=marker,
        evidence=Evidence(
            entity_id=entity_id,
            title=f"Doc {entity_id}",
            url=f"https://kb.test/{entity_id}",
            source_type="page",
            best_chunk_id=entity_id * 10,
            best_chunk_text="fragment",
        ),
        score=0.5,
    )


def test_markers_come_in_first_appearance_order_without_duplicates():
    assert used_markers("See [2], then [1], and [2] again") == [2, 1]


def test_resolve_maps_used_markers_to_their_evidence():
    trace, wire = resolve("Answer [1] and [3].", [packed(1, 11), packed(2, 22), packed(3, 33)])

    assert [c["marker"] for c in trace] == [1, 3]
    assert [c["entity_id"] for c in trace] == [11, 33]
    assert wire[0].title == "Doc 11"
    assert wire[1].snippet == "fragment"


def test_invented_marker_is_silently_dropped():
    trace, wire = resolve("Totally real source [7].", [packed(1, 11)])
    assert trace == []
    assert wire == []


def test_no_markers_no_citations():
    trace, wire = resolve("A plain answer.", [packed(1, 11)])
    assert (trace, wire) == ([], [])
