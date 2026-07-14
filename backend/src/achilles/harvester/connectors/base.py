"""Connector contract: 4 source-specific methods + a declarative manifest.

connectors.html#contract: fetch (extract) · normalize (transform station 1) ·
list_catalog (scope objects) · check_connection (stepped probe). Everything
after normalize is shared pipeline — a connector never touches the DB.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Self

from achilles.harvester.connectors.http import Throttle
from achilles.harvester.constants import RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier


@dataclass(frozen=True, slots=True)
class RawItem:
    """Extract → Transform contract: one source object, payload untouched."""

    source_type: str  # ticket · page · message · …
    source_entity_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScopeObject:
    """One catalog entry the admin can include/exclude (projects, spaces, channels)."""

    native_id: str
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class DiagnosisStep:
    name: str  # reachability · credentials · permissions
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """Stepped probe result; the first failed step names the fix."""

    steps: tuple[DiagnosisStep, ...]

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)


DIAGNOSIS_STEPS = ("reachability", "credentials", "permissions")
SKIPPED_DETAIL = "skipped"


def build_diagnosis(failed_step: str | None = None, detail: str = "") -> Diagnosis:
    """Steps before failed_step pass, it fails with detail, later steps are skipped."""
    steps: list[DiagnosisStep] = []
    failed = False
    for name in DIAGNOSIS_STEPS:
        if failed:
            steps.append(DiagnosisStep(name, ok=False, detail=SKIPPED_DETAIL))
        elif name == failed_step:
            steps.append(DiagnosisStep(name, ok=False, detail=detail))
            failed = True
        else:
            steps.append(DiagnosisStep(name, ok=True))
    return Diagnosis(tuple(steps))


@dataclass(frozen=True, slots=True)
class PrincipalDraft:
    """Person as the source presents them; email is the identity merge key."""

    source_user_id: str
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class GroupDraft:
    """Source container + its current membership snapshot (flat, v1)."""

    source_group_id: str
    name: str
    kind: str
    member_source_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AclNative:
    """Grant in the source's own terms; native_id targets a group or a principal."""

    scope: AclScope
    native_id: str | None = None


@dataclass(frozen=True, slots=True)
class LinkDraft:
    """In-source link claim; the runner resolves it to an edge or stages a ref."""

    relation: str  # source terms (blocks, mentions, child_of, …)
    target_kind: str  # issue · page · message · …
    target_ref: str  # native id of the target


@dataclass(frozen=True, slots=True)
class NormalizedEntity:
    """Transform contract — everything the shared pipeline needs, no source API left."""

    source_type: str
    source_entity_id: str
    title: str | None
    body: str | None  # flat text; the connector strips source markup
    url: str | None = None
    status: str | None = None  # EntityStatus value or None → classify fallback
    author: PrincipalDraft | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    acl: tuple[AclNative, ...] = ()
    links: tuple[LinkDraft, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    """Declarative connector self-description (connectors.html#manifest)."""

    type: str
    title: str
    needs_base_url: bool
    credential_label: str  # what the admin pastes (API token, bot token, …)
    scope_kinds: tuple[str, ...]  # what list_catalog enumerates
    incremental: bool = True
    # A real-time channel: the source POSTs an "object changed" event, the core
    # verifies it (freshness → signature → dedup) and the connector's
    # verify_webhook returns a delivery id — see BaseConnector.verify_webhook.
    webhooks: bool = False
    collection_toggles: tuple[str, ...] = ()  # recognised content_filters keys
    rate_limit_per_second: float = 1.0  # starting pace; AIMD learns from there
    rate_limit_scope: RateLimitScope = RateLimitScope.TENANT
    request_cost: int = 1
    default_authority: AuthorityTier = AuthorityTier.NORMAL
    # fetch() is globally non-decreasing by source_updated_at across the whole
    # stream (not just per container) — the precondition for resuming a failed
    # run from its watermark checkpoint. False → resume falls back to the cursor.
    ordered_stream: bool = True


class BaseConnector(ABC):
    """One instance per configured source, built by the runner per run.

    Subclasses receive an already-authenticated SourceHttpClient via their
    `create` factory (each connector knows its own auth header shape); the
    contract itself exposes no HTTP — an SDK-based connector plugs in the
    same way through the entry-point channel.
    """

    manifest: ClassVar[ConnectorManifest]

    def __init__(
        self,
        *,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> None:
        self.scope_mode = scope_mode
        self.scope_list = scope_list
        self.content_filters = dict(content_filters or {})

    def in_scope(self, native_id: str) -> bool:
        """All → scope_list is a deny-list; selected → an allow-list."""
        if self.scope_mode == "selected":
            return native_id in self.scope_list
        return native_id not in self.scope_list

    @classmethod
    @abstractmethod
    def create(
        cls,
        *,
        base_url: str | None,
        credential: str,
        throttle: Throttle | None = None,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> Self:
        """Build a ready instance — each connector knows its own auth header shape."""

    @classmethod
    def verify_webhook(
        cls,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        secret: str,
        now: float,
    ) -> str | None:
        """Authenticate one inbound webhook; return its delivery id or None.

        The delivery id is the dedup key — the same event delivered twice must
        return the same string, so the core drops the replay. Behaviour, not a
        value: the scheme (HMAC / static token / timestamp freshness) is the
        connector's own, never surfaced in the UI (security.html#webhooks).
        Default: no webhook support (manifest.webhooks is False).
        """
        del raw_body, headers, secret, now
        return None

    async def aclose(self) -> None:  # noqa: B027 — optional hook, default no-op
        """Release the underlying transport; default no-op."""

    @abstractmethod
    def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        """Stream changed objects; since=None → everything (full sync).

        Items MUST come in non-decreasing source_updated_at order where the
        source API allows ordering — the runner checkpoints a watermark and
        resumes with fetch(since=watermark); the idempotent Load makes the
        overlap re-read safe.
        """

    async def fetch_item(self, source_type: str, source_entity_id: str) -> RawItem | None:
        """Fetch one item by native id (DLQ retry, sync-modes.html#dlq-retry).

        Default None → targeted retry unsupported; the item drains at the next
        reconciliation sweep instead (any successful pass clears its DLQ row).
        """
        del source_type, source_entity_id
        return None

    @abstractmethod
    def normalize(self, raw: RawItem) -> NormalizedEntity:
        """Source payload → pipeline contract; the only source-specific transform."""

    @abstractmethod
    def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        """Stream people as the source knows them (acl-identity.html#identity)."""

    @abstractmethod
    def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        """Stream containers + membership snapshots (acl-identity.html#acl)."""

    @abstractmethod
    async def list_catalog(self) -> list[ScopeObject]:
        """Enumerate scope objects for the admin's include/exclude picker."""

    @abstractmethod
    async def check_connection(self) -> Diagnosis:
        """Stepped probe: reachability → credentials → permissions."""
