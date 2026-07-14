"""X-Request-Id: echo a safe inbound id or mint one; the problem envelope embeds it."""

import re
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_PREFIX = "req_"
_SAFE_INBOUND_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def mint_request_id() -> str:
    return _REQUEST_ID_PREFIX + secrets.token_hex(8)


class RequestIdMiddleware:
    """Outermost middleware: every response (including refusals) carries the id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = inbound if _SAFE_INBOUND_ID.fullmatch(inbound) else mint_request_id()
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
