"""Key-only identity for the external tier: JWT does not cross it (index.html#auth)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request, Response

from achilles.auth.dependencies import extract_api_key, resolve_key_request
from achilles.auth.models import User
from achilles.db.dependencies import DbSession


@dataclass(frozen=True, slots=True)
class KeyPrincipal:
    user: User
    scope: dict[str, Any]


async def get_key_principal(
    request: Request, response: Response, session: DbSession
) -> KeyPrincipal:
    token = extract_api_key(request)
    # POST is allowed here: the tier's contract is read-only by construction.
    key, user = await resolve_key_request(
        request, response, session, token, datetime.now(UTC), allow_mutating=True
    )
    return KeyPrincipal(user=user, scope=key.scope or {})


RequestKey = Annotated[KeyPrincipal, Depends(get_key_principal)]
