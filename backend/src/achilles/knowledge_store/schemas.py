"""Request/response contracts of the KS routes (index.html#api, tests.html).

Unknown fields → 422 (extra="forbid"); depth is bounded 1..3 at the schema layer;
an over-ceiling top_k is truncated server-side, not rejected.
"""

from pydantic import BaseModel, ConfigDict, Field

from achilles.api.serialization import UtcDateTime
from achilles.knowledge_store.constants import (
    DEFAULT_TOP_K,
    GRAPH_DEPTH_MAX,
    GRAPH_DEPTH_MIN,
    WINDOW_TIME_PATTERN,
    CadenceFrequency,
    EntityStatus,
    RelType,
)


class HitOut(BaseModel):
    entity_id: int
    score: float
    chunk_id: int | None = None  # lexical only
    depth: int | None = None  # graph only


class LexicalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1)


class GraphQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_ids: list[int] = Field(min_length=1)
    depth: int = Field(ge=GRAPH_DEPTH_MIN, le=GRAPH_DEPTH_MAX)
    rel_types: list[RelType] | None = None
    weight_min: float | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1)


class SqlFilterValues(BaseModel):
    """Closed filter list over the relational body — bound values only."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[int] | None = None
    source_types: list[str] | None = None
    statuses: list[EntityStatus] | None = None
    source_created_from: UtcDateTime | None = None
    source_created_to: UtcDateTime | None = None
    source_updated_from: UtcDateTime | None = None
    source_updated_to: UtcDateTime | None = None


class SqlQuery(SqlFilterValues):
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1)


class VectorQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1)


class HybridQuery(BaseModel):
    """Standalone query + optional value filters (hybrid-search.html#fusion)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    filters: SqlFilterValues | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1)


class FusedHitOut(BaseModel):
    entity_id: int
    score: float
    best_chunk_id: int | None = None


class HybridOut(BaseModel):
    hits: list[FusedHitOut]
    degraded: bool  # embedder was silent — text/graph/sql lists only


class SourceSlice(BaseModel):
    id: int
    name: str
    connector_type: str
    state: str
    entity_count: int
    chunk_count: int
    last_sync: UtcDateTime | None = None  # finished_at of the last terminal sync run


class SourcesOut(BaseModel):
    sources: list[SourceSlice]
    is_empty: bool  # the platform-level progressive-value property


class RunStarted(BaseModel):
    run_id: int


class SnapshotStarted(BaseModel):
    snapshot_id: int


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: int


class KnowledgeMetricsOut(BaseModel):
    """The five storage tiles (knowledge-store.html#storage-metrics)."""

    entities: int
    chunks: int
    edges: int
    pending_refs: int
    vector_bytes: int


class CurationRunOut(BaseModel):
    id: int
    trigger: str
    state: str
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    steps: dict[str, object] | None
    error: str | None
    created_at: UtcDateTime
    destructive_open: bool  # merge window is claimed — syncs are queuing behind it


class ReembedProgressOut(BaseModel):
    """Chunks refreshed vs total live — the "8.2k of 13.1k" panel line."""

    done: int
    total: int
    from_model: str | None  # display name of the model being retired
    to_model: str | None  # display name of the assigned target model


class CurationStatusOut(BaseModel):
    active: CurationRunOut | None  # trigger == model_change → it is a re-embed run
    reembed: ReembedProgressOut | None  # present only while a re-embed run is active
    last: CurationRunOut | None
    next_scheduled: UtcDateTime | None


class BackupSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    destination_url: str | None
    credential_is_set: bool
    frequency: str
    weekday: int | None
    time: str
    retention_count: int


class BackupSettingsPatch(BaseModel):
    """Partial update; credential is write-only: None = keep, "" = clear, text = re-encrypt."""

    model_config = ConfigDict(extra="forbid")

    destination_url: str | None = None
    credential: str | None = None
    frequency: CadenceFrequency | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    time: str | None = Field(default=None, pattern=WINDOW_TIME_PATTERN)
    retention_count: int | None = Field(default=None, gt=0)


class BackupSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    state: str
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
    size_bytes: int | None
    error: str | None
