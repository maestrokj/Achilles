"""AI Foundation domain vocabulary — ai-foundation/_workzone/data-model.html."""

from enum import StrEnum


class AiFunction(StrEnum):
    """The single functions dictionary (data-model.html#ai-functions).

    model_assignments CHECKs the system subset; model_usage CHECKs the full
    set — two constraints, one enum, divergence impossible by construction.
    """

    HARVESTER_EMBEDDING = "harvester_embedding"
    QUERY_RAG = "query_rag"
    AGENT_ENGINE = "agent_engine"
    CHAT = "chat"


# Functions that live as model_assignments rows (one model per function).
# Only embedding stays a system assignment; query_rag/chat/agent_engine resolve
# through the chat model in play (query_rag survives as a model_usage spend
# label only) and the chat_models/agent_models allow-lists.
SYSTEM_FUNCTIONS = (AiFunction.HARVESTER_EMBEDDING,)


class ProviderKind(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"
    PLATFORM = "platform"


class ProviderAdapter(StrEnum):
    """API dialect — decides the HTTP client shape and discovery call."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class ModelType(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"


class ModelOrigin(StrEnum):
    DISCOVERED = "discovered"
    MANUAL = "manual"
    BUILTIN = "builtin"


class CheckStatus(StrEnum):
    """Connectivity/probe outcome — shared by ai_providers and tools."""

    ACTIVE = "active"
    ERROR = "error"
    UNCHECKED = "unchecked"


class EmbedderRuntimeState(StrEnum):
    """Live phase of the assigned embedding model, as GET /admin/ai/embedder tells it.

    The first four mirror the built-in runtime's per-model states; the last two
    are backend verdicts: UNREACHABLE — the runtime gave no answer, EXTERNAL —
    a cloud embedder with no load phase to report.
    """

    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    UNREACHABLE = "unreachable"
    EXTERNAL = "external"


class ToolSource(StrEnum):
    PRESET = "preset"
    CUSTOM = "custom"
    MCP = "mcp"  # v2
    OPENAPI = "openapi"  # v2


class ToolAccess(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"  # v2


# v1 accepts only these subsets over the wire; the wider CHECK dictionaries
# above already carry the v2 values so no migration is needed to unlock them.
TOOL_SOURCES_V1 = (ToolSource.PRESET, ToolSource.CUSTOM)
TOOL_ACCESS_V1 = (ToolAccess.READ_ONLY,)

# --- Embeddings (knowledge-store/data-model.html#chunks, #ai-intrinsics) ---

# chunks.embedding is provisioned halfvec(EMBEDDING_DIM) once (stage-4
# migration); both builtin models are 1024-d. Assigning a model of another —
# or unknown — dimension is rejected until a dimension change becomes a
# schema operation (lifecycle.html#embedding-refresh, v2).
EMBEDDING_DIM = 1024

# --- Prompt (prompt-library.html, data-model.html#prompt-settings) ---

# Closed placeholder whitelist; an unknown {token} is rejected on save.
PROMPT_PLACEHOLDERS = frozenset({"org_name", "today"})
# Char-based cap (~1.5k tokens): long admin blocks eat the context window.
# The chat tokenizer arrives in stage 4; chars are the honest v1 measure.
PROMPT_MAX_CHARS = 6000

# --- Problem codes (api/problems.py convention: module-local, proximity) ---

CODE_MODEL_IN_USE = "MODEL_IN_USE"
CODE_SYSTEM_PROVIDER_PROTECTED = "SYSTEM_PROVIDER_PROTECTED"
CODE_MODEL_TYPE_MISMATCH = "MODEL_TYPE_MISMATCH"
CODE_LAST_DEFAULT_PROTECTED = "LAST_DEFAULT_PROTECTED"
CODE_PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
CODE_UNKNOWN_PLACEHOLDER = "UNKNOWN_PLACEHOLDER"
CODE_UNKNOWN_TOOL = "UNKNOWN_TOOL"
CODE_EMBEDDING_DIM_MISMATCH = "EMBEDDING_DIM_MISMATCH"
CODE_MODEL_TOO_LARGE = "MODEL_TOO_LARGE"
