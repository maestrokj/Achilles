"""Test-only routes that lock the layer contract before real domain routes exist.

New endpoints inherit these checks automatically (conformance is parametrized over
app.routes); the widgets router below only gives the suite something to bite on.
"""

import secrets
from datetime import UTC, datetime

import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from pydantic import BaseModel

from achilles.api.pagination import (
    DEFAULT_PAGE_SIZE,
    CursorParam,
    LimitParam,
    Page,
    decode_cursor,
    encode_cursor,
)
from achilles.api.problems import CODE_RATE_LIMITED, ApiError
from achilles.api.security_headers import SensitiveResponse
from achilles.api.serialization import UtcDateTime
from achilles.config import Settings
from achilles.infra.rate_limit import hit_sliding_window
from achilles.main import create_app

RATE_LIMIT_TEST_LIMIT = 2
RATE_LIMIT_TEST_WINDOW_SECONDS = 60


class Widget(BaseModel):
    id: int
    name: str
    created_at: UtcDateTime


class WidgetIn(BaseModel):
    name: str
    count: int


@pytest.fixture
def widget_store() -> list[Widget]:
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return [Widget(id=i, name=f"w{i}", created_at=fixed) for i in range(1, 8)]


@pytest.fixture
def app(test_settings: Settings, widget_store: list[Widget]) -> FastAPI:
    app = create_app(test_settings)
    router = APIRouter()
    rl_key = f"rl:test:{secrets.token_hex(4)}"

    async def list_widgets(
        limit: LimitParam = DEFAULT_PAGE_SIZE, cursor: CursorParam = None
    ) -> Page[Widget]:
        after = decode_cursor(cursor)[0] if cursor else 0
        ordered = sorted(widget_store, key=lambda w: w.id)
        chunk = [w for w in ordered if isinstance(after, int) and w.id > after][:limit]
        next_cursor = encode_cursor([chunk[-1].id]) if len(chunk) == limit else None
        return Page(items=chunk, next_cursor=next_cursor)

    async def echo_widget(widget: WidgetIn) -> WidgetIn:
        return widget

    async def limited(request: Request, response: Response) -> dict[str, str]:
        decision = await hit_sliding_window(
            request.state.redis.durable,
            rl_key,
            limit=RATE_LIMIT_TEST_LIMIT,
            window_seconds=RATE_LIMIT_TEST_WINDOW_SECONDS,
            now=datetime.now(UTC).timestamp(),
        )
        if not decision.allowed:
            raise ApiError(
                429,
                CODE_RATE_LIMITED,
                "Rate limited",
                "Request rate limit exceeded",
                retry_after=decision.retry_after,
            )
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return {"status": "ok"}

    async def auth_echo() -> dict[str, str]:
        return {"status": "ok"}

    router.add_api_route("/widgets", list_widgets, methods=["GET"])
    router.add_api_route("/widgets/echo", echo_widget, methods=["POST"])
    router.add_api_route("/limited", limited, methods=["GET"])
    router.add_api_route("/auth/echo", auth_echo, methods=["GET"], dependencies=[SensitiveResponse])
    app.include_router(router, prefix="/api/v1")
    return app
