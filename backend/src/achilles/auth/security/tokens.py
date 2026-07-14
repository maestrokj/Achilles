"""One-time token material: CSPRNG, SHA-256 at rest, constant-time compare.

Tokens are 256-bit random — SHA-256 (fast, unsalted) is enough at rest;
argon2 is for human passwords only. Design: protection.html#crypto-core.
"""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime

from achilles.auth.constants import (
    API_KEY_DISPLAY_PREFIX_LEN,
    API_KEY_PREFIX,
    LINK_CODE_ALPHABET,
    LINK_CODE_GROUP,
    LINK_CODE_LENGTH,
    TOKEN_NBYTES,
)

# The dash-grouped shape a code is *issued* in (K7P2-9XQ4). The separator is the
# disambiguator: it is what tells a link code from an ordinary short word like
# "DEADBEEF", which is otherwise all code-alphabet characters too.
_LINK_CODE_RE = re.compile(
    rf"[{LINK_CODE_ALPHABET}]{{{LINK_CODE_GROUP}}}(?:-[{LINK_CODE_ALPHABET}]{{{LINK_CODE_GROUP}}})+",
    re.IGNORECASE,
)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_NBYTES)


def generate_link_code() -> str:
    """Short, human-typeable one-time code, dash-grouped for readability (K7P2-9XQ4)."""
    body = "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))
    return "-".join(
        body[i : i + LINK_CODE_GROUP] for i in range(0, LINK_CODE_LENGTH, LINK_CODE_GROUP)
    )


def normalize_link_code(text: str) -> str:
    """Canonical form for hashing and compare: separators dropped, upper-cased.

    So the user may type the code lower-case, with or without the dash, and match.
    """
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def looks_like_link_code(text: str) -> bool:
    """Whether a DM has the shape of an issued link code (case forgiven, dash required).

    Chat bots use this to tell a link-code attempt from an ordinary message; the
    dash separators are the disambiguator, so a bare word is never taken for one.
    Confirmation itself still forgives a missing dash (see ``normalize_link_code``).
    """
    return _LINK_CODE_RE.fullmatch(text.strip()) is not None


def is_expired(expires_at: datetime, now: datetime) -> bool:
    """One expiry rule for every one-time token and session (tz-safe: TIMESTAMPTZ → UTC)."""
    return now >= expires_at.astimezone(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(known_hash: str, candidate_hash: str) -> bool:
    return hmac.compare_digest(known_hash, candidate_hash)


def generate_api_key() -> tuple[str, str, str]:
    """Return (key, key_hash, display_prefix); the raw key is shown exactly once."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(TOKEN_NBYTES)
    return key, hash_token(key), key[:API_KEY_DISPLAY_PREFIX_LEN]
