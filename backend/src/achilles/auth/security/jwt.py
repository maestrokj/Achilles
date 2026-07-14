"""Access-JWT: HS256, pinned algorithm list, kid pinned to the single active key.

Design: authentication.html#jwt-signing / #jwt-claims. The role claim is a snapshot
at issue time; critical checks re-read role and status from the DB.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import jwt as pyjwt

from achilles.auth.constants import (
    ACCESS_TOKEN_TTL,
    JWT_ACTIVE_KID,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_REQUIRED_CLAIMS,
)


class TokenExpiredError(Exception):
    """The signature is fine but the token is past exp."""


class TokenInvalidError(Exception):
    """Anything else: bad signature, wrong alg, missing claim, unknown kid."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: int
    role: str
    jti: str


def issue_access_token(
    *, user_id: int, role: str, secret: str, now: datetime, ttl: timedelta = ACCESS_TOKEN_TTL
) -> str:
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid7().hex,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return pyjwt.encode(
        payload,
        secret,
        algorithm=JWT_ALGORITHM,
        headers={"kid": JWT_ACTIVE_KID},
    )


def decode_access_token(token: str, *, secret: str) -> AccessClaims:
    try:
        # One active key for now; a real registry arrives with rotation (v2).
        kid = pyjwt.get_unverified_header(token).get("kid")
        if kid != JWT_ACTIVE_KID:
            raise TokenInvalidError from None
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],  # pinned — alg substitution dies here
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": list(JWT_REQUIRED_CLAIMS)},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise TokenExpiredError from exc
    except pyjwt.PyJWTError as exc:
        raise TokenInvalidError from exc

    try:
        return AccessClaims(
            user_id=int(payload["sub"]),
            role=str(payload["role"]),
            jti=str(payload["jti"]),
        )
    except (KeyError, ValueError) as exc:
        raise TokenInvalidError from exc
