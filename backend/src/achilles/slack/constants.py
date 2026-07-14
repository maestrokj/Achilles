"""Slack surface constants; the API base is shared with the harvester connector."""

SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_HTTP_TIMEOUT = 15.0  # seconds; the bot answers a person, not a batch

# Inbound webhook (slack/index.html: ack < 3 s, dedup, fail-closed window).
SIGNATURE_VERSION = "v0"
SIGNATURE_TIMESTAMP_TOLERANCE = 300  # seconds — Slack's replay window
CODE_SLACK_SIGNATURE_INVALID = "SLACK_SIGNATURE_INVALID"
CODE_SLACK_HOOK_UNAVAILABLE = "SLACK_HOOK_UNAVAILABLE"  # fail-closed: no Redis, no entry
INBOUND_JOB = "slack_inbound"
