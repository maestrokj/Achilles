"""Shared webhook verification primitives (security.html#webhooks).

The core runs freshness → signature → dedup in a fixed cheap→expensive order;
these helpers cover the signature and delivery-id steps a connector composes in
its verify_webhook. Comparisons are constant-time; the delivery id falls back to
a body fingerprint where the source sends no native id.
"""

import hashlib
import hmac


def hmac_sha256_hex(secret: str, body: bytes) -> str:
    """Lower-case hex HMAC-SHA256 of the raw body — the GitHub/Jira scheme."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_matches(secret: str, body: bytes, presented: str, *, prefix: str = "") -> bool:
    """Constant-time check of a presented HMAC signature (``prefix`` like ``sha256=``)."""
    expected = f"{prefix}{hmac_sha256_hex(secret, body)}"
    # latin-1: header bytes round-trip, and compare_digest never raises on them.
    return hmac.compare_digest(expected.encode("latin-1"), presented.encode("latin-1"))


def token_matches(secret: str, presented: str) -> bool:
    """Constant-time check of a shared static token (the GitLab scheme)."""
    return hmac.compare_digest(secret.encode(), presented.encode("latin-1"))


def body_fingerprint(body: bytes) -> str:
    """Delivery id of last resort: the body's own SHA-256.

    An identical replay hashes the same, a distinct event does not — enough for
    the dedup step where the source sends no native delivery id.
    """
    return hashlib.sha256(body).hexdigest()
