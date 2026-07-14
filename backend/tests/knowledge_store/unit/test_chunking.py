"""Chunker contract: deterministic fragments, per-fragment hash, token budget (unit)."""

import pytest

from achilles.knowledge_store.constants import CHUNK_TOKEN_BUDGET
from achilles.knowledge_store.services.chunking import chunk_body

pytestmark = [pytest.mark.unit]


def paragraph(word: str, count: int) -> str:
    return " ".join(f"{word}{i}" for i in range(count))


def test_empty_body_yields_no_fragments():
    assert chunk_body(None) == []
    assert chunk_body("") == []
    assert chunk_body("   \n\n  ") == []


def test_ordinals_are_contiguous_from_zero():
    body = "\n\n".join(paragraph("w", 300) for _ in range(3))
    drafts = chunk_body(body)
    assert [d.ordinal for d in drafts] == list(range(len(drafts)))
    assert len(drafts) > 1


def test_chunking_is_deterministic():
    body = "\n\n".join(paragraph("word", 250) for _ in range(4))
    assert chunk_body(body) == chunk_body(body)


def test_small_paragraphs_pack_into_one_fragment():
    body = "First paragraph.\n\nSecond paragraph.\n\nThird."
    drafts = chunk_body(body)
    assert len(drafts) == 1
    assert "First paragraph." in drafts[0].text
    assert "Third." in drafts[0].text


def test_fragment_stays_within_budget_for_splittable_text():
    body = "\n\n".join(paragraph("token", 150) for _ in range(10))
    for draft in chunk_body(body):
        assert draft.token_count <= CHUNK_TOKEN_BUDGET


def test_oversized_paragraph_splits_by_sentences():
    sentence = paragraph("s", 100) + "."
    body = " ".join(sentence for _ in range(8))  # one paragraph, ~800 tokens
    drafts = chunk_body(body)
    assert len(drafts) > 1
    for draft in drafts:
        assert draft.token_count <= CHUNK_TOKEN_BUDGET


def test_only_changed_fragment_moves_its_hash():
    first = paragraph("alpha", 300)
    second = paragraph("beta", 300)
    before = chunk_body(f"{first}\n\n{second}")
    after = chunk_body(f"{first}\n\n{second} changed")
    assert len(before) == len(after) == 2
    assert before[0].content_hash == after[0].content_hash
    assert before[1].content_hash != after[1].content_hash


def test_token_count_matches_whitespace_approximation():
    drafts = chunk_body("one two three")
    assert drafts[0].token_count == 3
