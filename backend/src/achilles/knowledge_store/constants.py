"""Knowledge Store fixed values — design: docs/architecture/modules/knowledge-store/."""

from enum import StrEnum

# --- Entities & projections (data-model.html) ---


class EntityStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"
    ARCHIVED = "archived"


class RelType(StrEnum):
    MENTIONS = "mentions"
    REPLIES_TO = "replies_to"
    LINKS_TO = "links_to"
    CHILD_OF = "child_of"
    DUPLICATE_OF = "duplicate_of"


class EdgeOrigin(StrEnum):
    HARVESTER = "harvester"
    CURATION = "curation"


# Connector `relation` terms → graph rel_type — shared by the Harvester runner
# (immediate edges) and Curation ref materialization; unknown terms → LINKS_TO.
REL_TYPE_BY_RELATION: dict[str, RelType] = {
    "child_of": RelType.CHILD_OF,
    "replies_to": RelType.REPLIES_TO,
    "duplicate": RelType.DUPLICATE_OF,
    "mentions": RelType.MENTIONS,
    "links_to": RelType.LINKS_TO,
}


# --- ACL (acl-identity.html) ---


class AclScope(StrEnum):
    GROUP = "group"
    PRINCIPAL = "principal"
    PUBLIC = "public"


# --- Sources — the table lives here, Harvester owns the writes (harvester/data-model.html) ---


class SourceState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISCONNECTED = "disconnected"


class AuthorityTier(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class AuthAccount(StrEnum):
    SERVICE = "service"
    PERSONAL = "personal"


class AuthMethod(StrEnum):
    STATIC_TOKEN = "static_token"  # noqa: S105 — enum label, not a secret
    OAUTH = "oauth"  # v2


class SourceScopeMode(StrEnum):
    ALL = "all"  # scope_list is a deny-list
    SELECTED = "selected"  # scope_list is an allow-list


class ProbeStatus(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"


# --- Run journals (lifecycle.html) ---


class CurationTrigger(StrEnum):
    SCHEDULE = "schedule"
    MODEL_CHANGE = "model_change"
    MANUAL = "manual"


class CurationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BackupState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CadenceFrequency(StrEnum):
    """Shared window cadence: backup_settings.frequency + curation_frequency."""

    DAILY = "daily"
    WEEKLY = "weekly"


class PlatformLocale(StrEnum):
    """Org-default UI locale (platform_settings.locale); users override per-profile."""

    RU = "ru"
    EN = "en"


class DateFormat(StrEnum):
    """Org-default date rendering; the backend is the source of the catalogue."""

    DMY_DOTS = "DD.MM.YYYY"
    MDY_SLASHES = "MM/DD/YYYY"
    ISO = "YYYY-MM-DD"


# The beat/reap pair (HEARTBEAT_INTERVAL, RUN_ZOMBIE_AFTER) is platform-wide
# and lives next to the reaper in infra/lifecycle.py.

# --- Curation Pass runtime (lifecycle.html#curation-pass) ---

# trust_score = authority x freshness x demand (lifecycle.html#staleness).
AUTHORITY_WEIGHT: dict[str, float] = {
    AuthorityTier.LOW: 0.7,
    AuthorityTier.NORMAL: 1.0,
    AuthorityTier.HIGH: 1.3,
}
TRUST_HALF_LIFE_DAYS = 180  # freshness = 2^(-age_days / half_life)
DEMAND_LOG_WEIGHT = 0.1  # demand = 1 + weight * ln(1 + hits)

CURATION_BATCH = 500  # refs per materialization batch
REEMBED_BATCH = 200  # chunks per re-embed batch
EMBED_WRITE_BATCH = 200  # chunks per embeddings call on the write path (page batch)

# HTTP budget for a background embed batch (ingest + re-embed). A full 200-chunk
# batch on a CPU embedder runs tens of seconds — an order above the online-search
# budget — plus a cold model's first call pays a one-off graph-compile (~7s). The
# online 10s timeout would fire mid-batch and the re-embed loop would misread the
# working-but-slow runtime as unready, retrying forever with zero progress.
EMBED_BATCH_TIMEOUT_SECONDS = 120.0

# The embedder warms lazily: right after a model switch the runtime answers 503
# while the new weights load (embedding-runtime.html#contention — the re-embed is
# background · can wait). The batch loop reads the runtime's own state instead of
# guessing by elapsed time: a model it reports as `loading` is waited out on the
# LOADING budget below (a first-time download is legitimately long), silence or
# `not_loaded` burns the stall budget, `error` fails the run at once.
REEMBED_RUNTIME_WAIT_SECONDS = 5.0
REEMBED_RUNTIME_MAX_RETRIES = 60  # ~5 min of unanswered runtime before giving up
REEMBED_LOADING_POLL_SECONDS = 5.0
REEMBED_LOADING_MAX_SECONDS = 1800.0  # a cold-cache weights download on a slow link

# Destructive gate (merge): retry window before the step is skipped
# (lifecycle.html#coordination — merge yields to running syncs, not vice versa).
DESTRUCTIVE_WAIT_RETRY_SECONDS = 10.0
DESTRUCTIVE_WAIT_CAP_SECONDS = 300.0

# --- FTS (data-model.html#chunks: 'simple' — no stemming, exact matches; vectors do semantics) ---

FTS_CONFIG = "simple"

# --- Chunking (data-model.html#chunks; real tokenizer arrives with the embedder, stage 3) ---

CHUNK_TOKEN_BUDGET = 400

# --- Retrieval contract (hybrid-search.html#primitives, tests.html) ---

DEFAULT_TOP_K = 20
MAX_TOP_K = 100  # server ceiling — over-ceiling requests are truncated
GRAPH_DEPTH_MIN = 1
GRAPH_DEPTH_MAX = 3
GRAPH_FANOUT_CAP = 50  # per node per step, applied before the ACL JOIN

# --- Hybrid fusion (hybrid-search.html#fusion) ---

RRF_K = 60  # reciprocal-rank smoothing: score = Σ 1/(RRF_K + rank)
HYBRID_GRAPH_SEEDS = 5  # top text-hit entities expanded one hop for context
HIDDEN_PROBE_K = 10  # unfiltered top checked for the content-free ACL hint

# --- Org-local schedule windows (backup_settings, curation_*; admin PATCH validation) ---

WINDOW_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"

# platform_settings.accent_color — shared by the admin PATCH schema and the DB CHECK.
ACCENT_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"

# --- Backups: how many recent snapshots the Admin screen lists ---

BACKUP_LIST_LIMIT = 20

# --- Error codes (generic ones live in api/problems.py) ---

CODE_RUN_ALREADY_ACTIVE = "RUN_ALREADY_ACTIVE"
CODE_RUN_ALREADY_FINISHED = "RUN_ALREADY_FINISHED"
CODE_REEMBED_IN_PROGRESS = "REEMBED_IN_PROGRESS"
CODE_BACKUP_NOT_CONFIGURED = "BACKUP_NOT_CONFIGURED"
CODE_MAINTENANCE = "MAINTENANCE"
CODE_EMBEDDINGS_UNAVAILABLE = "EMBEDDINGS_UNAVAILABLE"
