"""Slack connector: envelope, cursors, threads, ACL, diagnosis (unit, respx)."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from achilles.harvester.connectors.base import AclNative, GroupDraft, PrincipalDraft, RawItem
from achilles.harvester.connectors.http import SourceItemError, SourceUnavailableError
from achilles.harvester.connectors.slack import SlackConnector
from achilles.harvester.constants import DlqReason, RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier, EntityStatus

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://slack.com/api"

CHANNEL_PUBLIC: dict[str, object] = {"id": "C1", "name": "general", "is_private": False}
CHANNEL_PRIVATE: dict[str, object] = {"id": "C2", "name": "secrets", "is_private": True}


def _connector(
    *,
    scope_mode: str = "all",
    scope_list: tuple[str, ...] = (),
    content_filters: dict[str, object] | None = None,
) -> SlackConnector:
    return SlackConnector.create(
        base_url=None,
        credential="secret",
        scope_mode=scope_mode,
        scope_list=scope_list,
        content_filters=content_filters,
    )


def _ok(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, **payload})


def _msg(ts: str, user: str = "U1", **extra: object) -> dict[str, object]:
    return {"ts": ts, "user": user, "text": f"msg {ts}", **extra}


def _raw_message(
    *, channel: dict[str, object], replies: list[dict[str, object]] | None = None
) -> RawItem:
    message = _msg("1700000000.000100")
    return RawItem(
        source_type="message",
        source_entity_id=f"{channel['id']}:{message['ts']}",
        payload={"channel": channel, "message": message, "replies": replies or []},
    )


def test_manifest() -> None:
    manifest = SlackConnector.manifest
    assert manifest.type == "slack"
    assert not manifest.needs_base_url
    assert manifest.scope_kinds == ("channel",)
    assert manifest.collection_toggles == ("include_private",)
    assert manifest.rate_limit_scope == RateLimitScope.WORKSPACE_METHOD
    assert manifest.default_authority == AuthorityTier.LOW
    assert not manifest.ordered_stream  # newest-first pages per channel


@respx.mock
async def test_fetch_pages_history_and_filters_noise() -> None:
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": [CHANNEL_PUBLIC]}))
    history_route = respx.get(f"{BASE}/conversations.history").mock(
        side_effect=[
            _ok(
                {
                    "messages": [
                        _msg("300.000200"),
                        {"ts": "300.000100", "bot_id": "B1", "text": "bot noise"},
                        _msg("300.000050", subtype="channel_join"),
                    ],
                    "response_metadata": {"next_cursor": "cur1"},
                }
            ),
            _ok({"messages": [_msg("100.000100", user="U2")]}),
        ]
    )

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    # Bots and subtype events skipped; each page re-yielded oldest-first.
    assert [item.source_entity_id for item in items] == ["C1:300.000200", "C1:100.000100"]
    assert history_route.call_count == 2
    assert "oldest" not in history_route.calls[0].request.url.params
    assert history_route.calls[1].request.url.params["cursor"] == "cur1"


@respx.mock
async def test_fetch_since_passes_oldest_timestamp() -> None:
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": [CHANNEL_PUBLIC]}))
    history_route = respx.get(f"{BASE}/conversations.history").mock(
        return_value=_ok({"messages": []})
    )

    since = datetime(2026, 6, 1, tzinfo=UTC)
    connector = _connector()
    items = [item async for item in connector.fetch(since)]
    await connector.aclose()

    assert items == []
    assert history_route.calls[0].request.url.params["oldest"] == str(since.timestamp())


@respx.mock
async def test_fetch_pulls_thread_replies_without_parent_and_bots() -> None:
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": [CHANNEL_PUBLIC]}))
    parent = _msg("200.000100", thread_ts="200.000100")
    respx.get(f"{BASE}/conversations.history").mock(return_value=_ok({"messages": [parent]}))
    replies_route = respx.get(f"{BASE}/conversations.replies").mock(
        return_value=_ok(
            {
                "messages": [
                    parent,  # replies repeats the parent first
                    _msg("200.000200", user="U2", thread_ts="200.000100"),
                    {"ts": "200.000300", "bot_id": "B1", "text": "bot"},
                ]
            }
        )
    )

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    (item,) = items
    assert replies_route.calls[0].request.url.params["ts"] == "200.000100"
    assert [reply["ts"] for reply in item.payload["replies"]] == ["200.000200"]
    assert item.payload["channel"]["id"] == "C1"


@respx.mock
async def test_fetch_survives_failed_thread_replies() -> None:
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": [CHANNEL_PUBLIC]}))
    parent = _msg("200.000100", thread_ts="200.000100")
    respx.get(f"{BASE}/conversations.history").mock(return_value=_ok({"messages": [parent]}))
    # One thread's replies fail permanently — the parent must still flow.
    respx.get(f"{BASE}/conversations.replies").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
    )

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    (item,) = items
    assert item.source_entity_id == "C1:200.000100"
    assert item.payload["replies"] == []


@respx.mock
async def test_fetch_scope_selected_channels_only() -> None:
    respx.get(f"{BASE}/conversations.list").mock(
        return_value=_ok({"channels": [CHANNEL_PUBLIC, CHANNEL_PRIVATE]})
    )
    history_route = respx.get(f"{BASE}/conversations.history").mock(
        return_value=_ok({"messages": [_msg("1.000100")]})
    )

    connector = _connector(scope_mode="selected", scope_list=("C2",))
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert [item.source_entity_id for item in items] == ["C2:1.000100"]
    assert history_route.calls[0].request.url.params["channel"] == "C2"


@respx.mock
async def test_channel_types_follow_include_private_toggle() -> None:
    list_route = respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": []}))

    connector = _connector(content_filters={"include_private": True})
    await connector.list_catalog()
    await connector.aclose()
    assert list_route.calls[0].request.url.params["types"] == "public_channel,private_channel"

    connector = _connector()
    await connector.list_catalog()
    await connector.aclose()
    assert list_route.calls[1].request.url.params["types"] == "public_channel"


async def test_normalize_public_message_thread_body() -> None:
    connector = _connector()
    entity = connector.normalize(
        _raw_message(channel=CHANNEL_PUBLIC, replies=[_msg("1700000060.000100", user="U2")])
    )
    await connector.aclose()

    assert entity.title is None
    assert entity.url is None
    assert entity.status == EntityStatus.FINAL
    assert entity.body == "U1: msg 1700000000.000100\n\nU2: msg 1700000060.000100"
    assert entity.author == PrincipalDraft(source_user_id="U1")
    assert entity.source_created_at == datetime.fromtimestamp(1700000000.0001, tz=UTC)
    assert entity.source_updated_at == entity.source_created_at
    assert entity.acl == (AclNative(AclScope.PUBLIC),)
    assert entity.links == ()
    assert entity.meta == {"channel_id": "C1", "channel_name": "general"}


async def test_normalize_private_channel_acl() -> None:
    connector = _connector()
    entity = connector.normalize(_raw_message(channel=CHANNEL_PRIVATE))
    await connector.aclose()

    assert entity.acl == (AclNative(AclScope.GROUP, "C2"),)


@respx.mock
async def test_ok_false_ratelimited_raises_unavailable() -> None:
    respx.get(f"{BASE}/conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "ratelimited"})
    )

    connector = _connector()
    with pytest.raises(SourceUnavailableError):
        await connector.list_catalog()
    await connector.aclose()


@respx.mock
async def test_ok_false_error_raises_item_error() -> None:
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": [CHANNEL_PUBLIC]}))
    respx.get(f"{BASE}/conversations.history").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    connector = _connector()
    with pytest.raises(SourceItemError) as exc_info:
        _ = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert exc_info.value.reason == DlqReason.MALFORMED
    assert "channel_not_found" in exc_info.value.detail


@respx.mock
async def test_fetch_principals_cursor_and_bot_filter() -> None:
    users_route = respx.get(f"{BASE}/users.list").mock(
        side_effect=[
            _ok(
                {
                    "members": [
                        {
                            "id": "U1",
                            "name": "alice",
                            "real_name": "Alice",
                            "profile": {"email": "a@x.test"},
                        },
                        {"id": "U2", "name": "gone", "deleted": True, "profile": {}},
                        {"id": "U3", "name": "bot", "is_bot": True, "profile": {}},
                        {"id": "USLACKBOT", "name": "slackbot", "profile": {}},
                    ],
                    "response_metadata": {"next_cursor": "cur1"},
                }
            ),
            _ok({"members": [{"id": "U4", "name": "bob", "profile": {}}]}),
        ]
    )

    connector = _connector()
    principals = [principal async for principal in connector.fetch_principals()]
    await connector.aclose()

    assert principals == [
        PrincipalDraft("U1", "a@x.test", "Alice"),
        PrincipalDraft("U4", None, "bob"),
    ]
    assert users_route.calls[1].request.url.params["cursor"] == "cur1"


@respx.mock
async def test_fetch_groups_private_membership_public_open() -> None:
    respx.get(f"{BASE}/conversations.list").mock(
        return_value=_ok({"channels": [CHANNEL_PUBLIC, CHANNEL_PRIVATE]})
    )
    members_route = respx.get(f"{BASE}/conversations.members").mock(
        side_effect=[
            _ok({"members": ["U1", "U2"], "response_metadata": {"next_cursor": "cur1"}}),
            _ok({"members": ["U3"]}),
        ]
    )

    connector = _connector(content_filters={"include_private": True})
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    assert groups == [
        GroupDraft("C1", "general", "channel"),
        GroupDraft("C2", "secrets", "channel", ("U1", "U2", "U3")),
    ]
    assert members_route.calls[0].request.url.params["channel"] == "C2"
    assert members_route.calls[1].request.url.params["cursor"] == "cur1"


@respx.mock
async def test_list_catalog() -> None:
    respx.get(f"{BASE}/conversations.list").mock(
        return_value=_ok({"channels": [CHANNEL_PUBLIC, CHANNEL_PRIVATE]})
    )

    connector = _connector()
    catalog = await connector.list_catalog()
    await connector.aclose()

    assert [(obj.native_id, obj.name, obj.kind) for obj in catalog] == [
        ("C1", "general", "channel"),
        ("C2", "secrets", "channel"),
    ]


@respx.mock
async def test_check_connection_ok() -> None:
    respx.get(f"{BASE}/auth.test").mock(return_value=_ok({"team": "T1"}))
    respx.get(f"{BASE}/conversations.list").mock(return_value=_ok({"channels": []}))

    connector = _connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert diagnosis.ok
    assert [step.name for step in diagnosis.steps] == [
        "reachability",
        "credentials",
        "permissions",
    ]


@respx.mock
async def test_check_connection_invalid_auth_skips_later_steps() -> None:
    respx.get(f"{BASE}/auth.test").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )

    connector = _connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert not diagnosis.ok
    reachability, credentials, permissions = diagnosis.steps
    assert reachability.ok
    assert not credentials.ok
    assert "invalid_auth" in credentials.detail
    assert permissions.detail == "skipped"
