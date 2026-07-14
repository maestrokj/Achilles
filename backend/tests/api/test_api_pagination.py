"""Unified list contract: {items, next_cursor}, keyset cursor, server-side caps."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.api.conftest import Widget

pytestmark = [pytest.mark.api, pytest.mark.p1]


async def _collect_pages(client: AsyncClient, limit: int) -> list[list[int]]:
    pages: list[list[int]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/api/v1/widgets", params=params)).json()
        pages.append([w["id"] for w in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


async def test_single_page_has_no_cursor(client: AsyncClient):
    body = (await client.get("/api/v1/widgets")).json()
    assert {"items", "next_cursor"} <= body.keys()
    assert len(body["items"]) == 7
    assert body["next_cursor"] is None


async def test_cursor_walk_covers_everything_once(client: AsyncClient):
    pages = await _collect_pages(client, limit=3)
    flat = [i for page in pages for i in page]
    assert flat == sorted(flat), "ordering must be deterministic"
    assert len(flat) == len(set(flat)) == 7, "no duplicates, no losses"


async def test_limit_above_cap_is_422(client: AsyncClient):
    resp = await client.get("/api/v1/widgets", params={"limit": 101})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_limit_zero_is_422(client: AsyncClient):
    assert (await client.get("/api/v1/widgets", params={"limit": 0})).status_code == 422


async def test_malformed_cursor_is_422(client: AsyncClient):
    resp = await client.get("/api/v1/widgets", params={"cursor": "@@not-base64@@"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "cursor"


async def test_empty_list_cursor_is_422(client: AsyncClient):
    # base64url("[]") decodes to a well-formed but valueless cursor — must be a
    # 422, not an IndexError → 500 when the keyset reads its first value.
    resp = await client.get("/api/v1/widgets", params={"cursor": "W10"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_cursor_survives_insertion(client: AsyncClient, widget_store: list[Widget]):
    first = (await client.get("/api/v1/widgets", params={"limit": 3})).json()
    assert [w["id"] for w in first["items"]] == [1, 2, 3]

    widget_store.append(
        Widget(id=0, name="w0", created_at=datetime(2026, 1, 2, tzinfo=UTC)),
    )

    second = (
        await client.get("/api/v1/widgets", params={"limit": 3, "cursor": first["next_cursor"]})
    ).json()
    assert [w["id"] for w in second["items"]] == [4, 5, 6], (
        "keyset cursor must not shift on inserts before it"
    )
