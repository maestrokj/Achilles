"""Deterministic body→fragments chunker (data-model.html#chunks).

The token counter is injected: the real tokenizer of the assigned embedding
model when available (ai_foundation/services/tokenizer.py), else the built-in
whitespace approximation — the chunker itself never fails over counting.
"""

import hashlib
import re
from dataclasses import dataclass

from achilles.ai_foundation.services.tokenizer import TokenCounter
from achilles.knowledge_store.constants import CHUNK_TOKEN_BUDGET

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    text: str
    token_count: int
    content_hash: str


def _whitespace_count(text: str) -> int:
    return len(text.split())


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _pack_words(words: list[str], count: TokenCounter) -> list[str]:
    """Greedy word windows sized by the token budget.

    Last resort for a single over-budget sentence; a lone word above the
    budget stands as its own window.
    """
    pieces: list[str] = []
    window: list[str] = []
    window_tokens = 0
    for word in words:
        word_tokens = count(word)
        if window and window_tokens + word_tokens > CHUNK_TOKEN_BUDGET:
            pieces.append(" ".join(window))
            window, window_tokens = [], 0
        window.append(word)
        window_tokens += word_tokens
    if window:
        pieces.append(" ".join(window))
    return pieces


def _split_oversized(paragraph: str, count: TokenCounter) -> list[str]:
    """Sentence split for paragraphs over budget; hard word split as the last resort."""
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        tokens = count(sentence)
        if tokens > CHUNK_TOKEN_BUDGET:
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 0
            # Hard split packed by measured tokens, not word count: a subword
            # tokenizer counts >1 token per word, so word-sized windows would
            # overflow the budget. Summing per-word counts slightly overshoots
            # (cross-word merges), which keeps windows safely under budget.
            pieces.extend(_pack_words(sentence.split(), count))
            continue
        if current and current_tokens + tokens > CHUNK_TOKEN_BUDGET:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_body(body: str | None, *, token_counter: TokenCounter | None = None) -> list[ChunkDraft]:
    """Slice a body into ordered fragments packed to the token budget; empty body → []."""
    if body is None or not body.strip():
        return []
    count = token_counter or _whitespace_count

    fragments: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in (p.strip() for p in _PARAGRAPH_SPLIT.split(body)):
        if not paragraph:
            continue
        tokens = count(paragraph)
        if tokens > CHUNK_TOKEN_BUDGET:
            if current:
                fragments.append("\n\n".join(current))
                current, current_tokens = [], 0
            fragments.extend(_split_oversized(paragraph, count))
            continue
        if current and current_tokens + tokens > CHUNK_TOKEN_BUDGET:
            fragments.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(paragraph)
        current_tokens += tokens
    if current:
        fragments.append("\n\n".join(current))

    return [
        ChunkDraft(
            ordinal=i,
            text=text,
            token_count=count(text),
            content_hash=_content_hash(text),
        )
        for i, text in enumerate(fragments)
    ]
