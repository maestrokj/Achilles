"""argon2id hashing + the NIST 800-63B password policy (length · zxcvbn · HIBP).

Design: protection.html#crypto-core / #password-policy. One check set for every
entry point (register, invite, change, reset). No composition rules, no rotation.
"""

import asyncio
import functools
import hashlib
import logging
from typing import cast

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from zxcvbn import zxcvbn  # type: ignore[import-untyped]

from achilles.auth.constants import (
    ARGON2_MEMORY_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    ZXCVBN_MIN_SCORE,
)

logger = logging.getLogger(__name__)

HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"
HIBP_TIMEOUT_SECONDS = 2.0

_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError, InvalidHashError, Argon2Error:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current argon2 params — upgrade on next login."""
    return _hasher.check_needs_rehash(password_hash)


@functools.cache
def _dummy_hash() -> str:
    return hash_password("dummy-timing-equalizer")


def dummy_verify() -> None:
    """Burn the same argon2 work for unknown emails — login timing stays flat."""
    verify_password(_dummy_hash(), "definitely-not-the-password")


# argon2 at m=19 MiB / t=2 is tens of ms of pure CPU — run it off the event loop
# (argon2-cffi releases the GIL, so a worker thread genuinely unblocks the loop).


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)


async def dummy_verify_async() -> None:
    await asyncio.to_thread(dummy_verify)


def zxcvbn_score(password: str) -> int:
    result = cast("dict[str, object]", zxcvbn(password))
    return cast("int", result["score"])


async def is_password_compromised(password: str) -> bool:
    """HIBP k-anonymity check; unavailable → fail open (length+zxcvbn still apply)."""
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        async with httpx.AsyncClient(timeout=HIBP_TIMEOUT_SECONDS) as client:
            resp = await client.get(HIBP_RANGE_URL + prefix)
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("HIBP unavailable — password check degraded to length+zxcvbn")
        return False
    return any(line.split(":", 1)[0] == suffix for line in resp.text.splitlines())


async def validate_password(password: str) -> list[str]:
    """Return policy violations (empty = acceptable)."""
    violations: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        violations.append(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    elif len(password) > PASSWORD_MAX_LENGTH:
        violations.append(f"password must be at most {PASSWORD_MAX_LENGTH} characters")
    elif await asyncio.to_thread(zxcvbn_score, password) < ZXCVBN_MIN_SCORE:
        violations.append("password is too weak")
    elif await is_password_compromised(password):
        violations.append("password appears in known breaches")
    return violations
