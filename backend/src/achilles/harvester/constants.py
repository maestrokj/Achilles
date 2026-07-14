"""Harvester fixed values — design: docs/architecture/modules/harvester/.

Vocabulary of the sources control layer lives in knowledge_store/constants.py
(the table is there); this module owns the run/queue/connector vocabulary.
"""

from datetime import timedelta
from enum import StrEnum


class SyncMode(StrEnum):
    """Fetch strategy — how wide the `since` window opens (sync-modes.html)."""

    FULL = "full"
    INCREMENTAL = "incremental"
    RECONCILIATION = "reconciliation"


class SyncTrigger(StrEnum):
    """What raised the run; the row "type" is derived from mode+trigger+scope."""

    CONNECT = "connect"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    WATCHDOG = "watchdog"
    MANUAL = "manual"


class SyncState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"  # partial success = succeeded with error_count > 0
    FAILED = "failed"
    CANCELLED = "cancelled"


class DlqReason(StrEnum):
    """Why an item landed in dead_letters (data-model.html#dead-letters-table)."""

    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    MALFORMED = "malformed"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


class ErrorClass(StrEnum):
    """Connector error taxonomy: retry transient, dead-letter permanent."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


class RateLimitScope(StrEnum):
    """What one rate budget covers, per connector manifest (connectors.html#manifest)."""

    TENANT = "tenant"
    ACCOUNT_TOKEN = "account_token"  # noqa: S105 — enum label, not a secret
    WORKSPACE_METHOD = "workspace_method"
    SITE = "site"


class SourceHealth(StrEnum):
    """Derived, never stored: last run + probe → idle/queued/syncing/error."""

    IDLE = "idle"
    QUEUED = "queued"
    SYNCING = "syncing"
    ERROR = "error"


# --- Run resumption (reliability.html#checkpoint) ---

# A checkpoint older than this restarts the run from scratch: the source may
# have drifted too far for the saved page cursor to still be coherent.
CHECKPOINT_FRESHNESS = timedelta(hours=6)

# This many consecutive failed runs of one source raise the critical
# sync.run_failure_series notification (a single hiccup stays quiet).
SYNC_FAILURE_SERIES = 3

# --- Webhook intake (security.html#webhooks) ---

# Fail-closed sliding window per source, applied only after the signature
# passes (an anonymous flood must not consume a real source's budget).
WEBHOOK_RATE_LIMIT = 60
WEBHOOK_RATE_WINDOW_SECONDS = 60
# Delivery-id dedup horizon: one accepted event is a no-op for this long. Sized
# for the tokenless connectors (no timestamp freshness), where dedup alone
# holds replay — the timestamped ones self-expire far sooner via the signature.
WEBHOOK_DEDUP_TTL_SECONDS = 24 * 3600
# After a rotation the previous secret still verifies for this long — a
# zero-downtime cutover while the source is updated by hand.
WEBHOOK_GRACE_TTL_SECONDS = 24 * 3600
# A spike of rejected deliveries (failed signature) for one source raises a
# Security notification — the webhook analogue of the brute-force alert
# (security.html#webhooks). Counted in a fixed window; the alert fires once,
# when the count reaches the threshold. Mirrors the auth barrier's 10 / 15 min.
WEBHOOK_REJECT_ALERT_THRESHOLD = 10
WEBHOOK_REJECT_ALERT_WINDOW_SECONDS = 15 * 60

# --- Error codes (generic ones live in api/problems.py) ---

CODE_UNKNOWN_CONNECTOR = "UNKNOWN_CONNECTOR"
CODE_CONFIRM_MISMATCH = "CONFIRM_MISMATCH"
CODE_WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"
CODE_WEBHOOK_UNAVAILABLE = "WEBHOOK_UNAVAILABLE"
CODE_WEBHOOK_NOT_SUPPORTED = "WEBHOOK_NOT_SUPPORTED"
