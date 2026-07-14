"""Platform run-contract: one lifecycle for every run/state-machine journal.

Design: cache-workers/_workzone/lifecycle.html. The contract is a vocabulary,
not a fixed column set: consumers (sync_runs, curation_runs, agent_runs —
stages 2/5/6) declare their own journal columns, add the partial-UNIQUE
uniqueness lock in their migrations and register their tables in `RUN_TABLES`
for the singleton reaper.
"""

import asyncio
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

RUN_STATES = ("queued", "running", "succeeded", "failed", "stale")
RUN_ACTIVE_STATES = ("queued", "running")

MAX_ATTEMPTS = 5
BACKOFF_BASE = timedelta(seconds=1)
BACKOFF_CAP = timedelta(seconds=300)
# The beat/reap ratio is the contract: these three constants live together.
HEARTBEAT_INTERVAL = timedelta(seconds=30)
STALE_HEARTBEAT_AFTER = timedelta(minutes=5)  # 10 missed beats
# Journals that retry with a new run instead of a counter (curation_runs,
# sync_runs, agent_runs) free the lock after three missed beats.
RUN_ZOMBIE_AFTER = timedelta(seconds=90)
# A queued run waiting out a gate retries this often and gives up at the cap;
# the retry stays well under RUN_ZOMBIE_AFTER so the waiter beats before reap.
GATE_WAIT_RETRY = timedelta(seconds=10)
GATE_WAIT_CAP = timedelta(minutes=15)
# A queued row has no beat until a SAQ worker picks the job up, so its anchor
# is created_at and it may legitimately age in the lane backlog. Reaping it at
# RUN_ZOMBIE_AFTER would fail healthy backlog; the allowance covers the full
# designed wait (gate cap) plus the zombie margin — beyond that the publish is
# genuinely lost and the single-flight lock must be freed.
QUEUED_ZOMBIE_AFTER = GATE_WAIT_CAP + RUN_ZOMBIE_AFTER


class PermanentJobError(Exception):
    """Raise inside a job for non-retryable failures (bad config, revoked access)."""


# Both lane-gate transitions (sync mark_running, curation destructive acquire)
# take this xact-scoped advisory lock first — it kills the write-skew of the
# two facing NOT EXISTS gates (knowledge-store/lifecycle.html#coordination).
LANE_GATE_LOCK = "achilles:lane_gate"


async def advisory_xact_lock(session: AsyncSession, key: str = LANE_GATE_LOCK) -> None:
    """Serialize gate transactions on a named lock; released at commit/rollback."""
    lock_key = sa.func.hashtext(key)
    await session.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))


def run_state_check(table: str) -> sa.CheckConstraint:
    values = ",".join(f"'{s}'" for s in RUN_STATES)
    return sa.CheckConstraint(f"state IN ({values})", name=f"ck_{table}_state")


def uniqueness_lock_index(table: str, *scope_columns: str) -> sa.Index:
    """Partial UNIQUE over the scope while a run is active.

    The correctness lock lives in Postgres next to the journal, never in Redis
    (lifecycle.html#uniqueness).
    """
    active = ",".join(f"'{s}'" for s in RUN_ACTIVE_STATES)
    return sa.Index(
        f"uq_{table}_active",
        *scope_columns,
        unique=True,
        postgresql_where=sa.text(f"state IN ({active})"),
    )


def is_transient(error: BaseException) -> bool:
    return not isinstance(error, PermanentJobError)


@asynccontextmanager
async def heartbeat_loop(beat: Callable[[], Awaitable[None]]) -> AsyncGenerator[None]:
    """Run beat() immediately, then every HEARTBEAT_INTERVAL while the wrapped work runs.

    The immediate first beat matters: a run must not sit heartbeat-less for a
    whole interval, or the reaper's staleness anchor falls back to row age.
    """

    async def loop() -> None:
        while True:
            await beat()
            await asyncio.sleep(HEARTBEAT_INTERVAL.total_seconds())

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def db_beat(
    session_factory: async_sessionmaker[AsyncSession],
    beat: Callable[[AsyncSession], Awaitable[None]],
) -> Callable[[], Awaitable[None]]:
    """Adapt a per-session heartbeat statement to the loop's zero-arg contract."""

    async def run() -> None:
        async with session_factory() as session, session.begin():
            await beat(session)

    return run


async def wait_for_gate(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    try_start: Callable[[AsyncSession], Awaitable[bool]],
    get_state: Callable[[AsyncSession], Awaitable[str | None]],
    heartbeat: Callable[[AsyncSession], Awaitable[None]],
    queued_state: str,
    retry: timedelta = GATE_WAIT_RETRY,
    cap: timedelta = GATE_WAIT_CAP,
) -> bool | None:
    """Queued → running, waiting out a closed gate (lane window, concurrency cap).

    True → running; False → the run left queued (cancelled/reaped); None →
    the cap expired. Beats while waiting so the reaper's anchor stays fresh.
    """
    deadline = datetime.now(UTC) + cap
    while True:
        async with session_factory() as session, session.begin():
            if await try_start(session):
                return True
        async with session_factory() as session, session.begin():
            state = await get_state(session)
            if state != queued_state:
                return False
            await heartbeat(session)
        if datetime.now(UTC) >= deadline:
            return None
        await asyncio.sleep(retry.total_seconds())


def backoff_delay(attempt: int) -> timedelta:
    """Exponential with full jitter, capped (lifecycle.html#retry)."""
    ceiling = min(BACKOFF_BASE.total_seconds() * 2**attempt, BACKOFF_CAP.total_seconds())
    return timedelta(seconds=random.random() * ceiling)  # noqa: S311 — jitter, not crypto


def run_duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    """Elapsed run time in seconds; None until both timestamps are set.

    The shared measure for run-journal `LastRunOut` schemas (sync_runs,
    agent_runs) — the display schemas differ, only the arithmetic is common.
    Takes the two timestamps rather than the row: the journals are separate
    SQLAlchemy models with no common base to type a parameter against.
    """
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class RunTableSpec:
    """Per-table reaper contract.

    The platform run-contract is a vocabulary, not a fixed column set
    (cache-workers/lifecycle.html#contract) — consumers differ in terminal
    state, error column, staleness threshold and which states their
    single-flight lock covers. `anchor_columns` is the coalesce chain for
    the staleness timestamp: a run that dies before its first beat (lost
    publish, worker killed at start) still ages via started_at/created_at
    and cannot hold the lock forever. Every registered table also carries
    `finished_at` — the reaper stamps it on the terminal write. `extra_set`
    adds fixed column=value writes to that terminal UPDATE (agent_runs
    stamps its machine-readable reason there).
    """

    table: str
    reaped_state: str = "stale"
    error_column: str = "last_error"
    stale_after: timedelta = STALE_HEARTBEAT_AFTER
    active_states: tuple[str, ...] = ("running",)
    anchor_columns: tuple[str, ...] = ("heartbeat_at", "created_at")
    extra_set: tuple[tuple[str, str], ...] = ()


RUN_TABLES: tuple[RunTableSpec, ...] = (
    # KS/Harvester journals retry with a new run, not a counter: zombie → failed,
    # lock freed (knowledge-store/lifecycle.html#curation-runs, #backup).
    RunTableSpec(
        "curation_runs",
        reaped_state="failed",
        error_column="error",
        stale_after=RUN_ZOMBIE_AFTER,
        # The lock covers queued too — a queued row whose publish was lost
        # must be reaped as well, or reindex stays 409 forever.
        active_states=("queued", "running"),
        anchor_columns=("heartbeat_at", "started_at", "created_at"),
    ),
    RunTableSpec(
        "sync_runs",
        reaped_state="failed",
        error_column="error_detail",
        stale_after=RUN_ZOMBIE_AFTER,
        active_states=("queued", "running"),
        anchor_columns=("heartbeat_at", "started_at", "created_at"),
    ),
    RunTableSpec(
        "backup_snapshots",
        reaped_state="failed",
        error_column="error",
        stale_after=RUN_ZOMBIE_AFTER,
        anchor_columns=("heartbeat_at", "started_at", "created_at"),
    ),
    RunTableSpec(
        "agent_runs",
        reaped_state="failed",
        error_column="error",
        stale_after=RUN_ZOMBIE_AFTER,
        active_states=("queued", "running"),
        anchor_columns=("heartbeat_at", "started_at", "created_at"),
        # The journal distinguishes reap from an in-run failure by reason.
        extra_set=(("reason", "stale"),),
    ),
)


async def reap_stale_runs(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Terminate runs with a dead heartbeat per their table spec.

    Runs on the scheduler singleton only — one sweeper, N worker replicas must
    not race the cleanup.
    """
    now = now or datetime.now(UTC)
    swept = 0
    for spec in RUN_TABLES:
        anchor = f"coalesce({', '.join(spec.anchor_columns)})"
        extra = "".join(f", {column} = '{value}'" for column, value in spec.extra_set)
        # Queued rows have no beat yet (job not picked up) — they age on the
        # backlog allowance; every other active state ages on stale_after.
        beating = ",".join(f"'{s}'" for s in spec.active_states if s != "queued")
        predicate = f"(state IN ({beating}) AND {anchor} < :threshold)"
        if "queued" in spec.active_states:
            predicate += f" OR (state = 'queued' AND {anchor} < :queued_threshold)"
        result = await session.execute(
            sa.text(
                f"UPDATE {spec.table} "  # noqa: S608 — registry names, not user input
                f"SET state = :reaped_state, {spec.error_column} = 'heartbeat lost', "
                # A reaped run is terminal: stamp finished_at, or downstream
                # cadence logic reads the NULL as "never ran".
                f"finished_at = :now{extra} "
                f"WHERE {predicate}"
            ),
            {
                "reaped_state": spec.reaped_state,
                "now": now,
                "threshold": now - spec.stale_after,
                "queued_threshold": now - QUEUED_ZOMBIE_AFTER,
            },
        )
        swept += getattr(result, "rowcount", 0) or 0
    return swept
