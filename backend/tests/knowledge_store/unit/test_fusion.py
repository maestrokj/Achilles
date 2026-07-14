"""RRF fusion: rank-level merge folded to entities (hybrid-search.html#fusion, unit)."""

import pytest

from achilles.knowledge_store.retrieval.fusion import rrf
from achilles.knowledge_store.retrieval.hits import Hit

pytestmark = [pytest.mark.unit, pytest.mark.p1]

K = 10  # small smoothing keeps the arithmetic legible


def test_entity_in_both_lists_outranks_single_list_entities():
    vector = [Hit(entity_id=1, score=0.9, chunk_id=11), Hit(entity_id=2, score=0.8, chunk_id=21)]
    lexical = [Hit(entity_id=3, score=5.0, chunk_id=31), Hit(entity_id=1, score=4.0, chunk_id=12)]

    fused = rrf([vector, lexical], top_k=10, k=K)

    assert fused[0].entity_id == 1
    assert fused[0].score == pytest.approx(1 / (K + 1) + 1 / (K + 2))
    assert {hit.entity_id for hit in fused} == {1, 2, 3}


def test_chunks_fold_to_their_entity_at_the_best_rank():
    """Two fragments of one entity in one list: one contribution, best fragment kept."""
    hits = [
        Hit(entity_id=1, score=0.9, chunk_id=11),
        Hit(entity_id=1, score=0.8, chunk_id=12),
        Hit(entity_id=2, score=0.7, chunk_id=21),
    ]

    fused = rrf([hits], top_k=10, k=K)

    assert [hit.entity_id for hit in fused] == [1, 2]
    assert fused[0].score == pytest.approx(1 / (K + 1))  # rank 1 only, no double count
    assert fused[0].best_chunk_id == 11
    assert fused[1].score == pytest.approx(1 / (K + 3))  # folding does not shift ranks


def test_best_chunk_is_the_best_ranked_across_lists():
    vector = [Hit(entity_id=9, score=0.1, chunk_id=91)]  # rank 1
    lexical = [Hit(entity_id=8, score=2.0, chunk_id=81), Hit(entity_id=9, score=1.0, chunk_id=92)]

    fused = rrf([vector, lexical], top_k=10, k=K)

    nine = next(hit for hit in fused if hit.entity_id == 9)
    assert nine.best_chunk_id == 91


def test_rankless_lists_carry_no_evidence():
    """graph/sql hits have no chunk — they contribute score, not a fragment."""
    graph = [Hit(entity_id=5, score=0.5, depth=1)]

    fused = rrf([graph], top_k=10, k=K)

    assert fused[0].entity_id == 5
    assert fused[0].best_chunk_id is None


def test_ties_break_by_entity_id_for_a_stable_order():
    list_a = [Hit(entity_id=7, score=1.0)]
    list_b = [Hit(entity_id=3, score=1.0)]

    fused = rrf([list_a, list_b], top_k=10, k=K)

    assert [hit.entity_id for hit in fused] == [3, 7]


def test_top_k_truncates_after_the_merge():
    hits = [Hit(entity_id=i, score=1.0 / i) for i in range(1, 6)]

    fused = rrf([hits], top_k=2, k=K)

    assert [hit.entity_id for hit in fused] == [1, 2]


def test_no_lists_is_an_empty_result():
    assert rrf([], top_k=10, k=K) == []
