"""Citations: which packed markers the answer actually used (rag-pipeline.html).

The model is instructed to cite as [n]; only markers that appear in the final
text become citations — record-level, ordered by first appearance. The trace
stores links (ids + scores); the wire carries full cards. The replay rebuilds
the same cards from the stored links via the KS evidence hydrator.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.retrieval.evidence import fetch_evidence
from achilles.knowledge_store.retrieval.fusion import FusedHit
from achilles.query_engine.rag.search import PackedEvidence
from achilles.query_engine.schemas import CitationOut

_MARKER = re.compile(r"\[(\d{1,3})\]")

_UNKNOWN_SOURCE_TYPE = "unknown"  # the cited entity is gone (merged/purged)


def used_markers(text: str) -> list[int]:
    """Unique markers in order of first appearance."""
    return list(dict.fromkeys(int(match) for match in _MARKER.findall(text)))


def resolve(
    answer_text: str, packed: Sequence[PackedEvidence]
) -> tuple[list[dict[str, Any]], list[CitationOut]]:
    """(trace_citations, wire_citations) for the markers the answer used."""
    by_marker = {item.marker: item for item in packed}
    trace: list[dict[str, Any]] = []
    wire: list[CitationOut] = []
    for marker in used_markers(answer_text):
        item = by_marker.get(marker)
        if item is None:
            continue  # the model invented a number — silently not a citation
        trace.append(
            {
                "marker": marker,
                "entity_id": item.evidence.entity_id,
                "chunk_id": item.evidence.best_chunk_id,
                "score": item.score,
            }
        )
        wire.append(
            CitationOut(
                marker=marker,
                entity_id=item.evidence.entity_id,
                chunk_id=item.evidence.best_chunk_id,
                title=item.evidence.title,
                url=item.evidence.url,
                source_type=item.evidence.source_type,
                snippet=item.evidence.best_chunk_text,
            )
        )
    return trace, wire


def _parse_links(citations: object) -> list[tuple[int, int, int | None]]:
    """Stored trace links → (marker, entity_id, chunk_id); alien shapes drop."""
    links: list[tuple[int, int, int | None]] = []
    for item in citations if isinstance(citations, list) else []:
        if not isinstance(item, dict) or "entity_id" not in item:
            continue
        chunk_id = item.get("chunk_id")
        links.append(
            (
                int(str(item.get("marker", 0))),
                int(str(item["entity_id"])),
                int(str(chunk_id)) if chunk_id is not None else None,
            )
        )
    return links


async def hydrate(
    session: AsyncSession, stored: Iterable[tuple[int, object]]
) -> dict[int, list[CitationOut]]:
    """Replay: stored (message_id, trace_citations) → cards, per message.

    The trace stores links (#boundary); the cards hydrate from KS by id
    through the same evidence fetch the live turn uses — a merged/purged
    entity keeps its marker but drops its coordinates.
    """
    per_message = {message_id: _parse_links(citations) for message_id, citations in stored}
    pairs = list(
        dict.fromkeys(
            (entity_id, chunk_id)
            for links in per_message.values()
            for _, entity_id, chunk_id in links
        )
    )
    evidence = await fetch_evidence(
        session,
        [
            FusedHit(entity_id=entity_id, score=0.0, best_chunk_id=chunk_id)
            for entity_id, chunk_id in pairs
        ],
    )
    cards = {(item.entity_id, item.best_chunk_id): item for item in evidence}
    out: dict[int, list[CitationOut]] = {}
    for message_id, links in per_message.items():
        if not links:
            continue
        out[message_id] = [
            CitationOut(
                marker=marker,
                entity_id=entity_id,
                chunk_id=chunk_id,
                title=card.title if card else None,
                url=card.url if card else None,
                source_type=card.source_type if card else _UNKNOWN_SOURCE_TYPE,
                snippet=card.best_chunk_text if card else None,
            )
            for marker, entity_id, chunk_id in links
            for card in (cards.get((entity_id, chunk_id)),)
        ]
    return out
