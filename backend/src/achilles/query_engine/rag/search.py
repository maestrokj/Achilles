"""search_knowledge — the tool body: cache gate → KS hybrid → evidence → pack.

The standalone query is the model's argument; identity is never one — it
comes from the caller's session (retrieval.html#identity). The handler
returns packed fragments as *data* for the model and keeps the structured
outcome (trace candidates, markers, hidden hint) for the turn's finalization.

Parallel tool calls share one AsyncSession, which tolerates no concurrency —
the internal lock serializes DB work while keyless tools still run alongside.
"""

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.llm.types import ToolSpec
from achilles.knowledge_store.retrieval import hybrid
from achilles.knowledge_store.retrieval.evidence import Evidence, fetch_evidence
from achilles.knowledge_store.retrieval.sql import FILTERS_JSON_SCHEMA, SqlFilters, parse_filters
from achilles.query_engine.constants import SEARCH_TOP_K
from achilles.query_engine.conversation import budget
from achilles.query_engine.rag import cache_gate

SEARCH_KNOWLEDGE = "search_knowledge"

# Prompt-layer text — the model reads this, not the UI (i18n does not apply).
_DESCRIPTION = (
    "Search the company knowledge base. Write a standalone query: name the "
    "subject explicitly even when the user's message omits it. Optional value "
    "filters narrow by source type, status or update dates."
)

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Standalone search query"},
        "filters": FILTERS_JSON_SCHEMA,
    },
    "required": ["query"],
    "additionalProperties": False,
}

SEARCH_SPEC = ToolSpec(name=SEARCH_KNOWLEDGE, description=_DESCRIPTION, parameters=_PARAMETERS)

_EMPTY_ANSWER = "No matching records found in the knowledge base."


@dataclass(frozen=True, slots=True)
class PackedEvidence:
    marker: int
    evidence: Evidence
    score: float


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """One executed search — the finalization feed (trace, citations, grounding)."""

    search_query: str
    candidates: list[dict[str, Any]]  # links: KS ids + scores
    packed: list[PackedEvidence]
    degraded: bool
    # Carried for the lazy hidden-ACL probe (resolve_hidden_hint) so it reuses
    # the embedding instead of re-embedding; None on a cache hit or degrade.
    query_vector: list[float] | None
    embedding_model: str | None
    cache_hit: bool


class SearchKnowledgeTool:
    """Per-turn handler; markers continue across calls of one round."""

    def __init__(
        self,
        session: AsyncSession,
        cache: Redis,
        *,
        user_id: int,
        counter: budget.TokenCounter,
        evidence_budget: int,
    ) -> None:
        self._session = session
        self._cache = cache
        self._user_id = user_id
        self._counter = counter
        self._evidence_budget = evidence_budget
        self._lock = asyncio.Lock()
        self._next_marker = 1
        self.outcomes: list[SearchOutcome] = []

    async def __call__(self, *, query: str = "", filters: object = None) -> str:
        standalone = " ".join(str(query).split())
        if not standalone:
            return "Error: 'query' is required"
        async with self._lock:  # one AsyncSession — DB work must not interleave
            payload, query_vector, embedding_model = await self._lookup(
                standalone, parse_filters(filters)
            )
            return self._pack(standalone, payload, query_vector, embedding_model)

    async def _lookup(
        self, query: str, filters: SqlFilters | None
    ) -> tuple[dict[str, Any], list[float] | None, str | None]:
        # Filtered searches skip the cache: the key is query+identity only,
        # and a filtered result under a bare key would poison later turns.
        cacheable = filters is None
        if cacheable:
            cached = await cache_gate.get(self._cache, user_id=self._user_id, query=query)
            if cached is not None:
                # No vector on a hit — the lazy hidden probe degrades to lexical.
                return {**cached, "cache_hit": True}, None, None
        result = await hybrid.search(
            self._session,
            user_id=self._user_id,
            query=query,
            top_k=SEARCH_TOP_K,
            filters=filters,
        )
        evidence = await fetch_evidence(self._session, result.hits)
        payload: dict[str, Any] = {
            "candidates": [
                {"entity_id": hit.entity_id, "score": hit.score, "chunk_id": hit.best_chunk_id}
                for hit in result.hits
            ],
            "evidence": [asdict(item) for item in evidence],
            "degraded": result.degraded,
            "cache_hit": False,
        }
        # Degraded and empty results must not outlive their moment: cached, an
        # embedder outage would pin the vector-less answer, and an empty answer
        # would hide a freshly ingested document for the whole TTL. The query
        # vector rides beside the payload, never into it — the cache stays lean.
        if cacheable and not result.degraded and result.hits:
            await cache_gate.put(self._cache, user_id=self._user_id, query=query, payload=payload)
        return payload, result.query_vector, result.embedding_model

    def _pack(
        self,
        query: str,
        payload: dict[str, Any],
        query_vector: list[float] | None,
        embedding_model: str | None,
    ) -> str:
        scores = {c["entity_id"]: float(c["score"]) for c in payload["candidates"]}
        evidence = [Evidence(**item) for item in payload["evidence"]]
        fitted = budget.pack_evidence(
            evidence, counter=self._counter, budget_tokens=self._evidence_budget
        )
        packed: list[PackedEvidence] = []
        blocks: list[str] = []
        for item in fitted:
            marker = self._next_marker
            self._next_marker += 1
            packed.append(
                PackedEvidence(marker=marker, evidence=item, score=scores.get(item.entity_id, 0.0))
            )
            title = item.title or "(untitled)"
            lines = [f"[{marker}] {title} ({item.source_type})"]
            if item.url:
                lines.append(item.url)
            if item.best_chunk_text:
                lines.append(item.best_chunk_text)
            blocks.append("\n".join(lines))
        self.outcomes.append(
            SearchOutcome(
                search_query=query,
                candidates=payload["candidates"],
                packed=packed,
                degraded=bool(payload["degraded"]),
                query_vector=query_vector,
                embedding_model=embedding_model,
                cache_hit=bool(payload.get("cache_hit")),
            )
        )
        return "\n\n".join(blocks) if blocks else _EMPTY_ANSWER

    async def resolve_hidden_hint(self) -> hybrid.HiddenHint | None:
        """The ACL access-hint — computed only when the reply cited nothing.

        Deliberately off the answer's critical path: the turn finalizer calls
        this after the text has streamed, and only in the citation-less case
        the plaque is actually shown. Each search's stored query vector is
        reused (never re-embedded); the first search that surfaces a hidden top
        candidate wins. A cache hit carries no vector, so its probe degrades to
        the lexical half — the same soft degrade an embedder outage produces.
        """
        for outcome in self.outcomes:
            hint = await hybrid.hidden_hint(
                self._session,
                user_id=self._user_id,
                query=outcome.search_query,
                query_vector=outcome.query_vector,
                embedding_model=outcome.embedding_model,
            )
            if hint is not None:
                return hint
        return None
