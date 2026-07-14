"""Shared transform stations: filter + classify fallback (pipeline.html).

Source-specific work ends at normalize; these stations run identically for
every connector and mode. chunk/embed live in KS (upsert_entity), the v2
stations (contextualize) don't exist yet.
"""

from achilles.harvester.connectors.base import NormalizedEntity
from achilles.knowledge_store.constants import EntityStatus

_VALID_STATUSES = frozenset(str(s) for s in EntityStatus)


def keep(entity: NormalizedEntity) -> bool:
    """Filter station: drop items with no text at all (pipeline.html#filter).

    Bot/system noise is dropped earlier, in the connector's fetch — only it
    knows the source's markers; this station holds the source-agnostic floor.
    """
    return bool((entity.title or "").strip() or (entity.body or "").strip())


def classify(entity: NormalizedEntity) -> str:
    """Classify station: trust the connector's status, else FINAL.

    A living document with no source status signal is treated as final —
    draft/archived are deliberate source-side markers (pipeline.html#classify).
    """
    if entity.status in _VALID_STATUSES:
        return str(entity.status)
    return str(EntityStatus.FINAL)
