"""Rank-level fusion (hybrid-search.html#fusion): raw scores are incomparable.

RRF over the primitives' ranked lists, folded to entities — the merge key is
the entity; the best-ranked fragment stays as evidence. Pure function, no IO.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from achilles.knowledge_store.constants import RRF_K
from achilles.knowledge_store.retrieval.hits import Hit


@dataclass(frozen=True, slots=True)
class FusedHit:
    entity_id: int
    score: float
    best_chunk_id: int | None = None  # evidence fragment, when a text primitive hit


def rrf(ranked_lists: Sequence[Sequence[Hit]], *, top_k: int, k: int = RRF_K) -> list[FusedHit]:
    """Σ 1/(k + rank) per entity across lists; rank is 1-based within a list.

    Within one list an entity counts once at its best (first) rank — chunk
    hits fold to their parent. Ties break by entity_id for a stable order.
    """
    scores: dict[int, float] = {}
    evidence: dict[int, tuple[int, int]] = {}  # entity_id → (rank, chunk_id)
    for hits in ranked_lists:
        seen: set[int] = set()
        for rank, hit in enumerate(hits, start=1):
            if hit.entity_id in seen:
                continue
            seen.add(hit.entity_id)
            scores[hit.entity_id] = scores.get(hit.entity_id, 0.0) + 1.0 / (k + rank)
            if hit.chunk_id is not None:
                best = evidence.get(hit.entity_id)
                if best is None or rank < best[0]:
                    evidence[hit.entity_id] = (rank, hit.chunk_id)
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [
        FusedHit(
            entity_id=entity_id,
            score=score,
            best_chunk_id=evidence[entity_id][1] if entity_id in evidence else None,
        )
        for entity_id, score in ordered[:top_k]
    ]
