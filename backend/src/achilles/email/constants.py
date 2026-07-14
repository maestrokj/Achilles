"""Email module constants: transport security modes, error taxonomy, timeouts."""

from enum import StrEnum


class SmtpSecurity(StrEnum):
    NONE = "none"
    STARTTLS = "starttls"
    SSL_TLS = "ssl_tls"


class EmailKind(StrEnum):
    """Letter templates the compose step knows (templates.html)."""

    INVITE = "invite"
    RESET = "reset"
    TEST = "test"
    NOTIFICATION = "notification"


# The inline test probe answers within the request; queued sends afford more.
SMTP_TEST_TIMEOUT_SECONDS = 10.0
SMTP_SEND_TIMEOUT_SECONDS = 30.0

# Queued delivery: transient failures retry with exponential backoff
# (delivery.html#errors); a 5xx SMTP verdict is permanent — no retry.
SEND_MAX_RETRIES = 5
SEND_RETRY_BACKOFF_SECONDS = 30.0

# SAQ Job fields every queued send is published with: without them SAQ's
# default is a single attempt and the raise-to-retry contract does not exist.
SEND_RETRY_JOB_ARGS: dict[str, object] = {
    "retries": SEND_MAX_RETRIES,
    "retry_delay": SEND_RETRY_BACKOFF_SECONDS,
    "retry_backoff": True,  # 30s → 60s → 120s → …
}


class PermanentSendError(Exception):
    """The mailbox refused (SMTP 5xx) — retries are pointless."""


class TransientSendError(Exception):
    """Server unreachable, timeout, throttle (4xx / network) — retry with backoff."""
