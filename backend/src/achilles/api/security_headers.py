"""Security headers on every response — auth-security/_workzone/protection.html (station 2).

The nginx edge repeats these for static assets; the app owns them for API responses
so the guarantee holds in any deployment shape.

A response that carries credentials or tokens (login, refresh, invite-accept,
raw API key…) must never be cached and never leak a referrer. The route that
owns the secret declares that itself via the ``SensitiveResponse`` dependency —
the middleware only reads the marker, it knows no paths.
"""

from fastapi import Depends, Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HSTS = "max-age=31536000; includeSubDomains"
CSP = "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
REFERRER_POLICY_DEFAULT = "strict-origin-when-cross-origin"
REFERRER_POLICY_SENSITIVE = "no-referrer"

_SENSITIVE_STATE_KEY = "sensitive_response"

_COMMON_HEADERS = {
    "Strict-Transport-Security": HSTS,
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": PERMISSIONS_POLICY,
}


def mark_sensitive(request: Request) -> None:
    setattr(request.state, _SENSITIVE_STATE_KEY, True)


# Attach to a route or router: `dependencies=[SensitiveResponse]`.
SensitiveResponse = Depends(mark_sensitive)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _COMMON_HEADERS.items():
                    headers.setdefault(name, value)
                if scope.get("state", {}).get(_SENSITIVE_STATE_KEY, False):
                    headers["Referrer-Policy"] = REFERRER_POLICY_SENSITIVE
                    headers.setdefault("Cache-Control", "no-store")
                else:
                    headers.setdefault("Referrer-Policy", REFERRER_POLICY_DEFAULT)
            await send(message)

        await self.app(scope, receive, send_with_headers)
