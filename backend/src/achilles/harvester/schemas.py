"""Harvester API contracts (index.html#api, tests.html).

The credential is write-only: it comes in as plaintext, is encrypted at the
route and never returned — reads expose only credential_is_set.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from achilles.knowledge_store.constants import AuthAccount, AuthorityTier, SourceScopeMode


class SourceCreate(BaseModel):
    name: str = Field(min_length=1)
    connector_type: str
    base_url: str | None = None
    credential: str | None = None
    auth_account: AuthAccount = AuthAccount.SERVICE
    scope_mode: SourceScopeMode = SourceScopeMode.ALL
    scope_list: list[str] = Field(default_factory=list)
    content_filters: dict[str, Any] = Field(default_factory=dict)
    sync_interval: int | None = Field(default=None, gt=0, le=2_147_483_647)  # minutes
    reconcile_interval: int | None = Field(default=None, gt=0, le=2_147_483_647)  # days
    reconcile_window: int | None = Field(default=None, ge=0, le=10079)
    authority_tier: AuthorityTier | None = None  # None = manifest default


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    credential: str | None = None  # None = keep; "" = clear; text = re-encrypt
    state: str | None = None  # active | paused (disconnected is derived from creds)
    scope_mode: SourceScopeMode | None = None
    scope_list: list[str] | None = None
    content_filters: dict[str, Any] | None = None
    sync_interval: int | None = Field(default=None, gt=0, le=2_147_483_647)
    reconcile_interval: int | None = Field(default=None, gt=0, le=2_147_483_647)
    reconcile_window: int | None = Field(default=None, ge=0, le=10079)
    authority_tier: AuthorityTier | None = None
    webhook_enabled: bool | None = None  # real-time channel toggle; needs a secret first


class LastRunOut(BaseModel):
    """The table's "last run" summary — a slice of the sync_runs journal row."""

    state: str
    mode: str
    duration_seconds: float | None  # finished_at - started_at; None while not both set
    error: str | None  # error_detail of a failed run
    progress_done: int | None  # entities_done
    progress_total: int | None  # entities_total


class SourceOut(BaseModel):
    id: int
    name: str
    connector_type: str
    state: str
    health: str  # derived: idle | queued | syncing | error
    base_url: str | None
    auth_account: str
    credential_is_set: bool
    scope_mode: str
    scope_list: list[str]
    content_filters: dict[str, Any]
    sync_interval: int | None
    reconcile_interval: int | None
    reconcile_window: int | None
    authority_tier: str | None
    incremental_cursor: dict[str, Any] | None
    last_probe_at: datetime | None
    last_probe_status: str | None
    last_sync_at: datetime | None  # finished_at of the last terminal run
    last_run: LastRunOut | None  # the freshest journal row: active if any, else last terminal
    dlq_count: int  # queued dead letters — the table pill + retry affordance
    entity_count: int  # live entities this source contributed to the graph
    webhook_supported: bool  # the connector type accepts real-time events
    webhook_enabled: bool  # the real-time channel is switched on for this source
    webhook_secret_set: bool  # a signing secret exists (never the value)
    webhook_endpoint_url: str | None  # where the source POSTs — shown once supported
    created_at: datetime


class ConnectorTypeOut(BaseModel):
    """Wizard step 1: what a connector self-describes (connectors.html#manifest)."""

    type: str
    title: str
    needs_base_url: bool
    credential_label: str
    scope_kinds: list[str]
    collection_toggles: list[str]
    webhooks: bool  # the type accepts real-time events → show the webhook section


class WebhookSecretOut(BaseModel):
    """Rotate response: the new signing secret, shown once (later only masked)."""

    secret: str


class ProbeRequest(BaseModel):
    """Wizard step 3: try the draft connection before any source row exists."""

    connector_type: str
    base_url: str | None = None
    credential: str | None = None


class ScopeItem(BaseModel):
    native_id: str
    name: str
    kind: str


class CatalogOut(BaseModel):
    items: list[ScopeItem]


class DiagnosisStepOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class DiagnosisOut(BaseModel):
    ok: bool
    steps: list[DiagnosisStepOut]


class ProbeOut(BaseModel):
    ok: bool
    steps: list[DiagnosisStepOut]
    catalog: list[ScopeItem] | None  # present when the probe passed — wizard step 4


class SyncRequest(BaseModel):
    mode: str = "incremental"  # incremental | full — auto modes are not exposed


class SyncStarted(BaseModel):
    run_id: int


class FanOutStarted(BaseModel):
    run_ids: list[int]


class SyncRunOut(BaseModel):
    id: int
    mode: str
    trigger: str
    state: str
    entities_done: int | None
    entities_total: int | None
    error_count: int
    error_detail: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class DeadLetterOut(BaseModel):
    id: int
    source_type: str
    source_entity_id: str
    reason: str
    error_detail: str | None
    attempts: int
    updated_at: datetime


class DeleteConfirm(BaseModel):
    confirm: str  # must equal the source name (type-to-confirm)


class HealthOut(BaseModel):
    health: str
    state: str
    active_run_id: int | None
    last_probe_status: str | None
    last_probe_at: datetime | None
