"""Slack request signature: v0=HMAC_SHA256(secret, "v0:{ts}:{body}").

The timestamp tolerance closes the replay window; comparison is constant-time.
"""

import hashlib
import hmac

from achilles.slack.constants import SIGNATURE_TIMESTAMP_TOLERANCE, SIGNATURE_VERSION


def verify(signing_secret: str, *, timestamp: str, body: bytes, signature: str, now: float) -> bool:
    try:
        sent_at = float(timestamp)
    except ValueError:
        return False
    if abs(now - sent_at) > SIGNATURE_TIMESTAMP_TOLERANCE:
        return False
    base = f"{SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    # Compare as bytes: compare_digest rejects non-ASCII str, so a header with a
    # stray byte >0x7F would raise instead of failing the check. latin-1 is the
    # header codec Starlette decodes with, so it round-trips any byte.
    expected = f"{SIGNATURE_VERSION}={digest}".encode()
    return hmac.compare_digest(expected, signature.encode("latin-1"))
