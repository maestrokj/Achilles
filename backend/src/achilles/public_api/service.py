"""Search core shared by the external surfaces (Public API + MCP): findings, not prose."""

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.services.api_keys import parse_scope
from achilles.knowledge_store.retrieval import hybrid
from achilles.knowledge_store.retrieval.evidence import fetch_evidence
from achilles.public_api.constants import SNIPPET_MAX_CHARS
from achilles.public_api.schemas import SearchOut, SearchResultOut


def _snippet(text: str | None) -> str | None:
    if text is None or len(text) <= SNIPPET_MAX_CHARS:
        return text
    return text[:SNIPPET_MAX_CHARS].rstrip() + "…"


async def search_for_key(
    session: AsyncSession, *, user_id: int, scope: dict[str, object], query: str, limit: int
) -> SearchOut:
    """Hybrid search under the key owner's identity; the scope only narrows, ACL stays on top.

    The query text is deliberately never logged (public-api/index.html#governance).
    The hidden-ACL hint is an internal Query Engine contract and stays off this tier.
    """
    result = await hybrid.search(
        session,
        user_id=user_id,
        query=query,
        top_k=limit,
        allowed_source_ids=parse_scope(scope),
    )
    evidence = await fetch_evidence(session, result.hits)
    scores = {hit.entity_id: hit.score for hit in result.hits}
    return SearchOut(
        results=[
            SearchResultOut(
                title=item.title,
                snippet=_snippet(item.best_chunk_text),
                source=item.source_type,
                url=item.url,
                score=scores[item.entity_id],
            )
            for item in evidence
        ],
        degraded=result.degraded,
    )
