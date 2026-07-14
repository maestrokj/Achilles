"""Access-JWT unit cases — tests.html (P0, unit)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from achilles.auth.constants import JWT_AUDIENCE, JWT_ISSUER
from achilles.auth.security.jwt import (
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
    issue_access_token,
)

pytestmark = [pytest.mark.unit, pytest.mark.p0]

# PyJWT enforces RFC 7518 minimum HMAC key lengths (64 bytes covers the HS512 case too)
SECRET = "unit-test-secret-0123456789abcdef-0123456789abcdef-0123456789abcd"


def issue(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return issue_access_token(user_id=42, role="member", secret=SECRET, now=now)


def test_roundtrip():
    claims = decode_access_token(issue(), secret=SECRET)
    assert claims.user_id == 42
    assert claims.role == "member"
    assert claims.jti


def test_claims_present():
    payload = pyjwt.decode(issue(), options={"verify_signature": False})
    assert payload["sub"] == "42"
    assert payload["role"] == "member"
    assert payload["iss"] == JWT_ISSUER
    assert payload["aud"] == JWT_AUDIENCE
    assert payload["exp"] - payload["iat"] == 15 * 60
    assert payload["jti"]


def test_kid_header_present():
    header = pyjwt.get_unverified_header(issue())
    assert header["kid"] == "k1"
    assert header["alg"] == "HS256"


def test_expired_rejected():
    old = issue(now=datetime.now(UTC) - timedelta(minutes=16))
    with pytest.raises(TokenExpiredError):
        decode_access_token(old, secret=SECRET)


def test_wrong_key_rejected():
    with pytest.raises(TokenInvalidError):
        decode_access_token(issue(), secret="a-different-secret-0123456789abcdef")


def test_alg_substitution_rejected():
    """Tokens signed with any non-pinned algorithm must die, same secret or not."""
    payload = pyjwt.decode(issue(), options={"verify_signature": False})
    hs512 = pyjwt.encode(payload, SECRET, algorithm="HS512", headers={"kid": "k1"})
    with pytest.raises(TokenInvalidError):
        decode_access_token(hs512, secret=SECRET)


def test_alg_none_rejected():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": "k1"}).encode())
    payload = pyjwt.decode(issue(), options={"verify_signature": False})
    body = base64.urlsafe_b64encode(json.dumps(payload).encode())
    forged = b".".join((header.rstrip(b"="), body.rstrip(b"="), b"")).decode()
    with pytest.raises(TokenInvalidError):
        decode_access_token(forged, secret=SECRET)


def test_unknown_kid_rejected():
    token = pyjwt.encode(
        pyjwt.decode(issue(), options={"verify_signature": False}),
        SECRET,
        algorithm="HS256",
        headers={"kid": "k9"},
    )
    with pytest.raises(TokenInvalidError):
        decode_access_token(token, secret=SECRET)


@pytest.mark.parametrize("missing", ["sub", "role", "jti", "iat", "exp"])
def test_missing_claim_rejected(missing: str):
    payload = pyjwt.decode(issue(), options={"verify_signature": False})
    del payload[missing]
    token = pyjwt.encode(payload, SECRET, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises((TokenInvalidError, TokenExpiredError)):
        decode_access_token(token, secret=SECRET)


@pytest.mark.parametrize(("claim", "value"), [("iss", "impostor"), ("aud", "other-api")])
def test_issuer_audience_mismatch_rejected(claim: str, value: str):
    payload = pyjwt.decode(issue(), options={"verify_signature": False})
    payload[claim] = value
    token = pyjwt.encode(payload, SECRET, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(TokenInvalidError):
        decode_access_token(token, secret=SECRET)


def test_jti_unique_per_issue():
    jtis = {decode_access_token(issue(), secret=SECRET).jti for _ in range(20)}
    assert len(jtis) == 20
