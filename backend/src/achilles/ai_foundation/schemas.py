"""Request/response contracts of the AI registry routes (index.html#api, tests.html).

Secrets are write-only: api_key/credential come in as plaintext, are encrypted
at the service layer and never round-trip — providers answer with a ••••xxxx
mask, tools with a bare is_set flag.
"""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from achilles.ai_foundation.constants import (
    CheckStatus,
    EmbedderRuntimeState,
    ModelOrigin,
    ModelType,
    ProviderAdapter,
    ProviderKind,
)
from achilles.api.serialization import UtcDateTime

# --- Providers ---


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: ProviderKind = ProviderKind.CLOUD
    adapter: ProviderAdapter
    base_url: str | None = None
    api_key: str | None = None  # write-only


class ProviderCheckConfig(BaseModel):
    """Draft connection fields of ProviderCreate — probed stateless, nothing stored."""

    model_config = ConfigDict(extra="forbid")

    kind: ProviderKind = ProviderKind.CLOUD
    adapter: ProviderAdapter
    base_url: str | None = None
    api_key: str | None = None  # write-only, encrypted only in memory for the probe


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    api_key: str | None = None  # None → keep; "" → clear; text → re-encrypt


class ProviderOut(BaseModel):
    id: int
    name: str
    kind: ProviderKind
    adapter: ProviderAdapter
    base_url: str | None
    api_key_mask: str | None  # ••••xxxx; None → no key stored
    is_system: bool
    status: CheckStatus
    last_check_at: UtcDateTime | None


# --- Models ---


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: int
    model_id: str = Field(min_length=1)
    display_name: str | None = None  # defaults to model_id
    model_type: ModelType
    # builtin is migration-seeded, never accepted over the wire (422 by the Literal).
    origin: Literal[ModelOrigin.DISCOVERED, ModelOrigin.MANUAL] = ModelOrigin.MANUAL
    price_input: Decimal | None = Field(default=None, ge=0)
    price_output: Decimal | None = Field(default=None, ge=0)
    meta: dict[str, Any] | None = None


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1)
    model_type: ModelType | None = None  # change refused (409) while the model is in use
    is_enabled: bool | None = None
    price_input: Decimal | None = Field(default=None, ge=0)
    price_output: Decimal | None = Field(default=None, ge=0)
    meta: dict[str, Any] | None = None


class ModelOut(BaseModel):
    id: int
    provider_id: int
    model_id: str
    display_name: str
    model_type: ModelType
    origin: str
    is_enabled: bool
    price_input: Decimal | None
    price_output: Decimal | None
    meta: dict[str, Any] | None


# --- Assignments (one PATCH for the whole board; absent field → untouched) ---


class ModelListItem(BaseModel):
    """One entry of a chat/agent allow-list: a model with its paused flag."""

    model_config = ConfigDict(extra="forbid")

    id: int  # ai_models.id
    is_enabled: bool = True


class ModelListPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ModelListItem]
    default: int | None = None  # must reference an enabled item when any is enabled


class AssignmentsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harvester_embedding: int | None = None
    chat_models: ModelListPatch | None = None
    agent_models: ModelListPatch | None = None


class ModelListOut(BaseModel):
    items: list[ModelListItem]
    default: int | None


class AssignmentsOut(BaseModel):
    harvester_embedding: int | None
    chat_models: ModelListOut
    agent_models: ModelListOut
    # The width the KS `chunks.embedding` column is provisioned to (halfvec(N)).
    # Fixed in v1; the screen gates embedder options against it (backend is the
    # source of truth — the front-end never hardcodes the dimension).
    embedding_dim: int


class EmbedderAssignedOut(BaseModel):
    model_pk: int
    model_id: str
    display_name: str


class EmbedderRuntimeOut(BaseModel):
    state: EmbedderRuntimeState
    error: str | None = None


class EmbedderStatusOut(BaseModel):
    """The embedding-model lifecycle as the Admin screens show it.

    `assigned` is the registry fact; `runtime` is the built-in runtime's live
    phase (None until something is assigned). Together they drive the
    not-assigned → loading weights → re-indexing → ready chip.
    """

    assigned: EmbedderAssignedOut | None
    runtime: EmbedderRuntimeOut | None


# --- Discovery / connectivity ---


class DiscoveredModel(BaseModel):
    model_id: str
    display_name: str | None = None
    # Best-effort type from discovery: authoritative where the provider tells us
    # (Google), inferred from the id otherwise. The admin can override it inline.
    model_type: ModelType = ModelType.CHAT


class DiscoveryOut(BaseModel):
    models: list[DiscoveredModel]


class CheckOut(BaseModel):
    status: CheckStatus
    last_check_at: UtcDateTime


# --- Tools (tool-catalog.html) ---


class ToolCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)  # must be a registered tool type
    config: dict[str, Any] | None = None
    credential: str | None = None  # write-only


class ToolPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_enabled: bool | None = None
    agents_allowed: bool | None = None
    config: dict[str, Any] | None = None
    credential: str | None = None  # None → keep; "" → clear; text → re-encrypt


class ToolOut(BaseModel):
    id: int | None  # None → registered type without an instance row yet
    name: str
    source: str
    access: str
    config: dict[str, Any] | None
    credential_is_set: bool  # the secret itself never leaves the DB
    needs_credential: bool
    chat_enabled: bool
    agents_allowed: bool
    status: CheckStatus
    last_check_at: UtcDateTime | None
    parameters: dict[str, Any]  # manifest JSON Schema of the call arguments


# --- Prompt (prompt-library.html) ---


class PromptBlockOut(BaseModel):
    text: str  # effective: the admin override, else the built-in locale default
    is_default: bool


class PromptOut(BaseModel):
    safety: PromptBlockOut
    org: PromptBlockOut


class PromptPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safety_text: str | None = None  # null/"" → reset to the built-in default
    org_text: str | None = None
