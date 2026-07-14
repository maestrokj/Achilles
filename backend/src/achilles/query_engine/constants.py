"""Query Engine fixed values — design: docs/architecture/modules/query-engine/."""

from enum import StrEnum

# --- Dialogue (data-model.html) ---


class Surface(StrEnum):
    """Where the conversation lives; v1 serves web, the rest join in stages 8+."""

    WEB = "web"
    SLACK = "slack"
    TELEGRAM = "telegram"
    MATTERMOST = "mattermost"
    MCP = "mcp"
    EXTENSION = "extension"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class FinishReason(StrEnum):
    """How an assistant turn ended; NULL on the row = completed cleanly.

    A terminal marker outlives the stream so a reload stays honest: a failed
    turn shows its notice (never a silent dangling question), a stopped one
    admits it was cut short instead of masquerading as a whole answer.
    """

    STOPPED = "stopped"  # user cancelled mid-stream — partial text kept
    FAILED = "failed"  # the turn errored — error_code carries the reason


FEEDBACK_VALUES = (-1, 1)

# Auto-generated from the first user message — no LLM call for a label.
TITLE_MAX_CHARS = 60

# --- Context budget (conversation.html#context-budget) ---
# One conservative window shared by every chat model in v1; per-model windows
# arrive when ai_models.meta carries them for chat types. Tokens are counted
# by the builtin tokenizer when assigned, else chars/4.
# History and retrieved context share ONE pool (CONTEXT_SHARED_TOKENS): history
# trims first under its own ceiling, evidence packs into the remainder it leaves
# — a short dialogue lends its slack to grounding. The response reserve stays
# protected outside the pool.

CONTEXT_SHARED_TOKENS = 10_000  # shared pool: history + retrieved context
HISTORY_BUDGET_TOKENS = 6000  # history's ceiling in the pool; evidence floor = pool minus this
RESPONSE_RESERVE_TOKENS = 2000  # protected: also the LLM max_tokens

# Newest messages fetched per turn — plenty to overfill the token budget
# without dragging a years-long conversation out of the DB every time.
HISTORY_FETCH_LIMIT = 200

# --- RAG route (rag-pipeline.html) ---

SEARCH_TOP_K = 20  # candidates fetched from KS hybrid before packing
RAG_CACHE_TTL = 600  # exact cache: standalone query + identity, seconds
MAX_MESSAGE_CHARS = 32_000  # wire ceiling for one user message

# --- Error codes (generic ones live in api/problems.py) ---

CODE_MODEL_NOT_ALLOWED = "MODEL_NOT_ALLOWED"
CODE_NO_CHAT_MODEL = "NO_CHAT_MODEL"
CODE_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
