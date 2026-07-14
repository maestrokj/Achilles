"""Common hit shape: ranked result of any primitive, folded to entities by the caller."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Hit:
    entity_id: int
    score: float
    chunk_id: int | None = None  # lexical only
    depth: int | None = None  # graph only
