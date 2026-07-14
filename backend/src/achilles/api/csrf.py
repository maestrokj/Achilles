"""CSRF barrier, v1: Origin/Referer check on every mutation.

Pairs with ``SameSite=Strict`` on the refresh cookie — protection.html#csrf.
Requests without both headers pass: non-browser clients (curl, SDKs) send neither,
and the cookie itself is useless cross-site under SameSite=Strict.
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from achilles.api.problems import CODE_FORBIDDEN, problem_response

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None:
            referer = headers.get("referer")
            if referer:
                parts = urlsplit(referer)
                origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme else referer

        if origin is not None and origin not in self.allowed:
            request_id: str = scope.get("state", {}).get("request_id", "")
            response = problem_response(
                request_id,
                status=403,
                code=CODE_FORBIDDEN,
                title="Forbidden",
                detail="Request origin is not allowed",
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
