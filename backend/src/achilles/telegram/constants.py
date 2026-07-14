"""Telegram surface constants (telegram/index.html)."""

from achilles.api import API_V1
from achilles.infra.redis import PREFIX_CACHE

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_HTTP_TIMEOUT = 15.0  # seconds; the bot answers a person, not a batch

# The public path Telegram posts updates to — the URL registered via setWebhook
# is `public_base_url + WEBHOOK_PATH`. Composed from the API base so a prefix
# change moves the registered URL with the route (api/__init__.py owns it).
WEBHOOK_PATH = f"{API_V1}/telegram/webhook"

# Inbound webhook (telegram/index.html: ack now, dedup by update_id, fail-closed).
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 — header name, not a secret
WEBHOOK_SECRET_BYTES = 32  # secrets.token_urlsafe entropy for the setWebhook secret
CODE_TELEGRAM_SECRET_INVALID = "TELEGRAM_SECRET_INVALID"  # noqa: S105 — error code, not a secret
CODE_TELEGRAM_HOOK_UNAVAILABLE = "TELEGRAM_HOOK_UNAVAILABLE"  # fail-closed: no Redis, no entry

# Enable failures — the switch reaches Telegram (setWebhook) and can be refused.
# These surface as problem+json so the admin sees *why* the bot didn't turn on,
# rather than a switch that silently sticks on with no delivery behind it.
CODE_TELEGRAM_WEBHOOK_NOT_PUBLIC = (
    "TELEGRAM_WEBHOOK_NOT_PUBLIC"  # PUBLIC_BASE_URL isn't a reachable HTTPS host
)
CODE_TELEGRAM_WEBHOOK_FAILED = (
    "TELEGRAM_WEBHOOK_FAILED"  # Telegram refused setWebhook (bad token, etc.)
)

# Live-probe verdicts stamped into last_test_ok / returned as TelegramTestOut.error.
# The probe measures real *delivery* readiness, not just a valid token: a healthy
# token whose webhook isn't registered on a public URL is not "connected".
TEST_ERR_NO_TOKEN = "no_token"  # noqa: S105 — verdict string, not a secret
TEST_ERR_NETWORK = "network_error"
TEST_ERR_WEBHOOK_NOT_PUBLIC = "webhook_not_public"
TEST_ERR_WEBHOOK_MISSING = "webhook_missing"
INBOUND_JOB = "telegram_inbound"

# Command that opens a fresh conversation (telegram/index.html#conversation).
NEW_CONVERSATION_COMMAND = "/new"

# cache · which conversation a chat is currently continuing (`/new` clears it).
# The conversations themselves persist in Postgres; this pointer is derived and
# evictable — a lost pointer just starts the next message in a new conversation.
ACTIVE_CONV_KEY = PREFIX_CACHE + "telegram:active-conv:{chat_id}"
ACTIVE_CONV_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — bound stale-chat growth
