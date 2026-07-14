"""Notifications domain constants: enums + the event catalog.

The catalog is the one home of per-event policy (dispatcher.html#catalog):
category, severity, addressing and the personal-email default. Platform
categories default email on (opt-out), personal ones off (opt-in).
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class EventType(StrEnum):
    """The seven route/pref categories — the matrix rows."""

    SYNC = "sync"
    SECURITY = "security"
    BUDGET = "budget"
    SYSTEM = "system"
    DISCOVERY = "discovery"
    AGENT = "agent"
    ACCOUNT = "account"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ChannelKind(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class WebhookPreset(StrEnum):
    SLACK = "slack"
    GENERIC = "generic"


class DeliveryState(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"  # in_app only


# Fixed in v1; per-type tuning is deliberately deferred (dispatcher.html#dedup).
DEDUP_WINDOW_DEFAULT = timedelta(minutes=30)

# Deliveries stuck in `queued` past this age are re-published by the cron
# sweep — insurance for the commit→enqueue gap.
STUCK_DELIVERY_AGE = timedelta(minutes=10)

API_KEY_EXPIRY_HORIZON = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class EventSpec:
    """One catalog row: where the event routes and how it behaves."""

    event_type: EventType
    severity: Severity
    source: str  # originating module slug (the feed's "who raised it")
    targeted: bool  # False → broadcast to active Owner/Admin
    dedup_window: timedelta = DEDUP_WINDOW_DEFAULT


def _platform(event_type: EventType, severity: Severity, source: str, **kw: timedelta) -> EventSpec:
    return EventSpec(event_type, severity, source, targeted=False, **kw)


def _personal(event_type: EventType, severity: Severity, source: str, **kw: timedelta) -> EventSpec:
    return EventSpec(event_type, severity, source, targeted=True, **kw)


# Personal categories are the member-visible slice of the prefs screen;
# org categories exist for Owner/Admin only (broadcast addressing).
PERSONAL_TYPES = (EventType.AGENT, EventType.ACCOUNT)
ORG_TYPES = tuple(t for t in EventType if t not in PERSONAL_TYPES)

# Personal-email default when no prefs row exists: platform categories
# opt-out (True), personal ones opt-in (False) — a category-level policy.
EMAIL_DEFAULTS: dict[EventType, bool] = {t: t not in PERSONAL_TYPES for t in EventType}

_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


def type_severity(event_type: EventType) -> Severity:
    """The category's badge severity: the loudest event of that type in the catalog.

    A category without catalog events yet (discovery) reads as info. Served from
    the precomputed TYPE_SEVERITY map — no per-call scan of the catalog.
    """
    return TYPE_SEVERITY[event_type]


# Keys double as the server-side i18n keys (notifications/i18n.py).
EVENT_CATALOG: dict[str, EventSpec] = {
    # --- Sync (harvester → admins) ---
    "sync.run_failure_series": _platform(EventType.SYNC, Severity.CRITICAL, "harvester"),
    "sync.source_unreachable": _platform(EventType.SYNC, Severity.CRITICAL, "harvester"),
    "sync.run_with_losses": _platform(EventType.SYNC, Severity.WARNING, "harvester"),
    # --- Security (auth → admins) ---
    "security.brute_force": _platform(EventType.SECURITY, Severity.CRITICAL, "auth"),
    "security.role_changed": _platform(EventType.SECURITY, Severity.CRITICAL, "auth"),
    "security.api_key_expiring": _platform(
        EventType.SECURITY, Severity.WARNING, "auth", dedup_window=timedelta(days=8)
    ),
    "security.webhook_rejected": _platform(
        EventType.SECURITY, Severity.WARNING, "harvester", dedup_window=timedelta(hours=1)
    ),
    # --- Budget (cron → admins) ---
    "budget.ai_monthly_exceeded": _platform(
        EventType.BUDGET, Severity.CRITICAL, "admin", dedup_window=timedelta(days=45)
    ),
    # --- System (platform health → admins) ---
    "system.provider_unavailable": _platform(EventType.SYSTEM, Severity.CRITICAL, "admin"),
    "system.backup_failed": _platform(EventType.SYSTEM, Severity.CRITICAL, "knowledge_store"),
    "system.curation_failed": _platform(EventType.SYSTEM, Severity.WARNING, "knowledge_store"),
    # --- Agent (agent_engine → the owner) ---
    "agent.run_failed": _personal(EventType.AGENT, Severity.WARNING, "agent_engine"),
    "agent.budget_exhausted": _personal(
        EventType.AGENT, Severity.INFO, "agent_engine", dedup_window=timedelta(days=8)
    ),
    "agent.admin_paused": _personal(EventType.AGENT, Severity.INFO, "agent_engine"),
    "agent.model_removed": _personal(EventType.AGENT, Severity.WARNING, "agent_engine"),
    # --- Account (auth → the person) ---
    "account.role_changed": _personal(EventType.ACCOUNT, Severity.INFO, "auth"),
    "account.temp_password": _personal(EventType.ACCOUNT, Severity.WARNING, "auth"),
}


# Precomputed once: each category's badge severity (loudest catalog event, info
# when the category has none yet). type_severity() and list_routes() read this
# map instead of rescanning the catalog per row.
TYPE_SEVERITY: dict[EventType, Severity] = {
    event_type: max(
        (spec.severity for spec in EVENT_CATALOG.values() if spec.event_type == event_type),
        key=_SEVERITY_RANK.__getitem__,
        default=Severity.INFO,
    )
    for event_type in EventType
}
