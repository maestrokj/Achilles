"""Mattermost surface constants (mattermost/index.html)."""

from achilles.infra.redis import PREFIX_CACHE

MATTERMOST_HTTP_TIMEOUT = 15.0  # seconds; the bot answers a person, not a batch
API_PATH = "/api/v4"  # the stable API the whole integration targets

INBOUND_JOB = "mattermost_inbound"

# Enable failure — the switch reaches the Mattermost server (a live token probe)
# and can be refused. Surfaces as problem+json so the admin sees *why* the bot
# didn't turn on, rather than a switch that silently sticks on a dead token.
CODE_MATTERMOST_ENABLE_FAILED = "MATTERMOST_ENABLE_FAILED"

# Live-probe verdicts stamped into last_test_ok / returned as MattermostTestOut.error.
# The probe vouches for the token and the server being reachable; *delivery*
# health is the listener's connected flag — the card shows both.
TEST_ERR_NO_TOKEN = "no_token"  # noqa: S105 — verdict string, not a secret
TEST_ERR_NO_BASE_URL = "no_base_url"
TEST_ERR_NETWORK = "network_error"

# The singleton listener (scheduler process): poll cadence, reconnect backoff.
LISTENER_POLL_SECONDS = 30.0  # settings re-read cadence; also how fast a PATCH lands
LISTENER_BACKOFF_START_SECONDS = 1.0
LISTENER_BACKOFF_MAX_SECONDS = 60.0
LISTENER_HEALTHY_RESET_SECONDS = 60.0  # a connection this old resets the backoff

# cache · listener health for the admin card. TTL > poll cadence so a healthy
# listener always renews in time; an expired key honestly reads as "not running".
LISTENER_STATUS_KEY = PREFIX_CACHE + "mattermost:listener"
LISTENER_STATUS_TTL_SECONDS = 90
