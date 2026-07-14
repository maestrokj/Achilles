"""Agent Engine fixed values — design: docs/architecture/modules/agent-engine/.

Run limits that an admin tunes live in platform_settings (iteration cap,
concurrency, weekly budget); here is the module-owned vocabulary and the
non-tunable mechanics.
"""

from enum import StrEnum


class AgentRunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class AgentRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Terminal outside the single-flight lock predicate: the engine took the
    # start but a runtime gate closed (budget / overlap).
    SKIPPED = "skipped"


class AgentRunReason(StrEnum):
    """Machine code detailing skipped/failed (data-model.html#agent-runs)."""

    BUDGET_EXCEEDED = "budget_exceeded"  # skipped
    ALREADY_RUNNING = "already_running"  # skipped
    ITERATION_CAP = "iteration_cap"  # failed
    ERROR = "error"  # failed
    STALE = "stale"  # failed — reaped after a dead heartbeat


class AgentStatus(StrEnum):
    """Derived, never stored: the summary chip on the agent card."""

    ACTIVE = "active"
    DISABLED = "disabled"
    ADMIN_PAUSED = "admin_paused"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_MISSING = "model_missing"


class ScheduleKind(StrEnum):
    INTERVAL = "interval"
    CALENDAR = "calendar"


class CalendarCadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


# Both concurrency-gate transitions serialize on one advisory lock — kills the
# write-skew of two facing COUNT(running) checks (execution.html#concurrency).
AGENT_GATE_LOCK = "achilles:agent_gate"

# Ceiling for one assistant round of the loop; the iteration cap bounds the
# rounds, this bounds a single response.
LOOP_ROUND_MAX_TOKENS = 4096

# The weekly budget window resets Sunday 00:00 org time; ISO weekday of the
# anchor (Monday=0 ... Sunday=6).
WEEK_RESET_WEEKDAY = 6

# --- Error codes (generic ones live in api/problems.py) ---

CODE_AGENT_RUN_ACTIVE = "AGENT_RUN_ACTIVE"
CODE_AGENT_BUDGET_EXCEEDED = "AGENT_BUDGET_EXCEEDED"
CODE_AGENT_NOT_RUNNABLE = "AGENT_NOT_RUNNABLE"
