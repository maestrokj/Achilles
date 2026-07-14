"""Token-window budget: history trims from the end, evidence packs the rest (unit)."""

import pytest

from achilles.ai_foundation.services.tokenizer import approx_counter
from achilles.knowledge_store.retrieval.evidence import Evidence
from achilles.query_engine.conversation import budget
from achilles.query_engine.conversation.store import autogen_title

pytestmark = [pytest.mark.unit, pytest.mark.p1]


def words(text: str) -> int:
    return len(text.split())


def test_history_keeps_the_newest_turns_that_fit():
    turns = [
        ("user", "one two three"),  # 3 tokens — the oldest, falls off
        ("assistant", "four five"),  # 2
        ("user", "six"),  # 1
    ]

    kept, used = budget.trim_history(turns, counter=words, budget_tokens=3)

    assert [(m.role, m.content) for m in kept] == [("assistant", "four five"), ("user", "six")]
    assert used == 3


def test_whole_history_fits_untouched():
    turns = [("user", "hi"), ("assistant", "hello")]
    kept, _ = budget.trim_history(turns, counter=words, budget_tokens=100)
    assert len(kept) == 2


def test_oversized_first_turn_means_empty_history_not_a_crash():
    kept, used = budget.trim_history([("user", "a " * 500)], counter=words, budget_tokens=10)
    assert kept == []
    assert used == 0


def test_history_reports_the_tokens_it_spent_so_evidence_gets_the_remainder():
    # 2 + 3 = 5 tokens spent of a 100-token ceiling; the caller lends the rest.
    turns = [("user", "one two"), ("assistant", "three four five")]
    kept, used = budget.trim_history(turns, counter=words, budget_tokens=100)
    assert len(kept) == 2
    assert used == 5


def _evidence(entity_id: int, text: str) -> Evidence:
    return Evidence(
        entity_id=entity_id,
        title=f"Doc {entity_id}",
        url=None,
        source_type="page",
        best_chunk_id=entity_id * 10,
        best_chunk_text=text,
    )


def test_evidence_packs_best_ranked_first_and_skips_what_does_not_fit():
    items = [
        _evidence(1, "one two three four"),  # 4 tokens
        _evidence(2, "five six seven eight nine"),  # 5 — does not fit after #1
        _evidence(3, "ten"),  # 1 — still fits
    ]

    packed = budget.pack_evidence(items, counter=words, budget_tokens=5)

    assert [item.entity_id for item in packed] == [1, 3]


def test_approx_counter_never_answers_zero():
    assert approx_counter("") == 1
    assert approx_counter("abcd" * 10) == 10


def test_title_collapses_whitespace_and_truncates_with_an_ellipsis():
    assert autogen_title("  hello   world  ") == "hello world"
    long = autogen_title("word " * 40)
    assert len(long) <= 60
    assert long.endswith("…")
