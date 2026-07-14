"""Token-window budget (conversation.html#context-budget).

The window splits into: system prompt (fixed) · history (grows — trimmed here,
oldest turns first) · the current message (always whole) · retrieved context
(packed into what remains) · a protected response reserve. Trimming loses
nothing: the words stay in messages, the facts are re-retrievable.
"""

from collections.abc import Sequence

from achilles.ai_foundation.llm.types import ChatMessage
from achilles.ai_foundation.services.tokenizer import TokenCounter
from achilles.knowledge_store.retrieval.evidence import Evidence
from achilles.query_engine.constants import CONTEXT_SHARED_TOKENS


def trim_history(
    turns: Sequence[tuple[str, str]],  # (role, content), oldest first
    *,
    counter: TokenCounter,
    budget_tokens: int,
) -> tuple[list[ChatMessage], int]:
    """Keep the newest turns that fit; return them with the tokens they spent.

    The spent count lets the caller hand history's unused slack to evidence —
    the two share one pool (conversation.html#context-budget). The current
    message is not part of this.
    """
    kept: list[ChatMessage] = []
    remaining = budget_tokens
    for role, content in reversed(turns):
        cost = counter(content)
        if cost > remaining:
            break
        remaining -= cost
        kept.append(ChatMessage(role="user" if role == "user" else "assistant", content=content))
    kept.reverse()
    return kept, budget_tokens - remaining


def evidence_budget(history_used: int) -> int:
    """Retrieved context gets the shared pool left by the trimmed history.

    History and evidence share CONTEXT_SHARED_TOKENS; because trim_history caps
    history at HISTORY_BUDGET_TOKENS (< the pool), the remainder never falls
    below the pool's evidence floor (conversation.html#context-budget) — no
    clamp needed, and a short dialogue lends its slack to grounding.
    """
    return CONTEXT_SHARED_TOKENS - history_used


def pack_evidence(
    evidence: Sequence[Evidence], *, counter: TokenCounter, budget_tokens: int
) -> list[Evidence]:
    """Best-ranked fragments first until the retrieved-context budget is spent."""
    packed: list[Evidence] = []
    remaining = budget_tokens
    for item in evidence:
        cost = counter(item.best_chunk_text or item.title or "")
        if cost > remaining:
            continue  # a shorter lower-ranked fragment may still fit
        remaining -= cost
        packed.append(item)
    return packed
