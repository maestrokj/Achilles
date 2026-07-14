"""Slack signature verification: HMAC, replay window, malformed input (unit)."""

import hashlib
import hmac

import pytest

from achilles.slack import signature

pytestmark = [pytest.mark.unit, pytest.mark.p1]

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
BODY = b'{"type":"event_callback"}'
NOW = 1_531_420_618.0


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256)
    return f"v0={digest.hexdigest()}"


def test_valid_signature_passes():
    ts = str(int(NOW))
    assert signature.verify(
        SECRET, timestamp=ts, body=BODY, signature=_sign(SECRET, ts, BODY), now=NOW
    )


def test_wrong_secret_fails():
    ts = str(int(NOW))
    assert not signature.verify(
        SECRET, timestamp=ts, body=BODY, signature=_sign("other", ts, BODY), now=NOW
    )


def test_tampered_body_fails():
    ts = str(int(NOW))
    assert not signature.verify(
        SECRET, timestamp=ts, body=b"{}", signature=_sign(SECRET, ts, BODY), now=NOW
    )


def test_stale_timestamp_fails_replay():
    ts = str(int(NOW) - 301)
    assert not signature.verify(
        SECRET, timestamp=ts, body=BODY, signature=_sign(SECRET, ts, BODY), now=NOW
    )


def test_malformed_timestamp_fails():
    assert not signature.verify(
        SECRET, timestamp="not-a-number", body=BODY, signature="v0=abc", now=NOW
    )


def test_non_ascii_signature_fails_without_raising():
    # A header byte >0x7F must fail the check, not crash compare_digest.
    ts = str(int(NOW))
    assert not signature.verify(SECRET, timestamp=ts, body=BODY, signature="v0=ÿabc", now=NOW)
