"""Slack connector: channel messages with threads folded in (connectors.html#slack).

The Web API answers HTTP 200 with {"ok": false, "error": ...} on failure, so
error handling lives in the envelope unwrap (_call), not in HTTP status codes.
Pagination is cursor-based: response_metadata.next_cursor, empty string = done.
Slack is SaaS — base_url is fixed, the manifest says needs_base_url=False.
"""

import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Self, cast

from achilles.harvester.connectors import webhook_verify
from achilles.harvester.connectors.base import (
    AclNative,
    BaseConnector,
    ConnectorManifest,
    Diagnosis,
    GroupDraft,
    NormalizedEntity,
    PrincipalDraft,
    RawItem,
    ScopeObject,
    build_diagnosis,
)
from achilles.harvester.connectors.http import (
    SourceHttpClient,
    SourceItemError,
    SourceUnavailableError,
    Throttle,
)
from achilles.harvester.connectors.registry import register_connector
from achilles.harvester.constants import DlqReason, RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier, EntityStatus
from achilles.slack import signature as slack_signature
from achilles.slack.constants import SLACK_API_BASE_URL

logger = logging.getLogger(__name__)

CHANNELS_PAGE_LIMIT = 200
HISTORY_PAGE_LIMIT = 100
USERS_PAGE_LIMIT = 200
INCLUDE_PRIVATE_TOGGLE = "include_private"

_RATE_LIMITED_ERROR = "ratelimited"
_BUILTIN_BOT_USER_ID = "USLACKBOT"


def _next_cursor(payload: dict[str, object]) -> str:
    meta = cast("dict[str, object]", payload.get("response_metadata") or {})
    return str(meta.get("next_cursor") or "")


def _is_noise(message: dict[str, object]) -> bool:
    """Joins/topic changes carry a subtype; bot posts carry bot_id — both skipped."""
    return bool(message.get("subtype")) or bool(message.get("bot_id"))


def _ts_to_datetime(value: object) -> datetime | None:
    """Slack ts (epoch seconds as string) → aware UTC datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(float(str(value)), tz=UTC)


def _render_line(message: dict[str, object]) -> str:
    """One body line: "<user>: <text>" — raw user ids in v1, name resolution is v2."""
    speaker = str(message.get("user") or "unknown")
    return f"{speaker}: {message.get('text') or ''}"


@register_connector
class SlackConnector(BaseConnector):
    """Channels → messages; a thread parent and its replies form one item."""

    manifest = ConnectorManifest(
        type="slack",
        title="Slack",
        needs_base_url=False,
        credential_label="Bot token",
        scope_kinds=("channel",),
        incremental=True,
        webhooks=True,
        collection_toggles=(INCLUDE_PRIVATE_TOGGLE,),
        rate_limit_per_second=0.5,
        rate_limit_scope=RateLimitScope.WORKSPACE_METHOD,
        default_authority=AuthorityTier.LOW,
        # Per-channel iteration over newest-first history pages — the whole
        # fetch is not globally ordered by source_updated_at.
        ordered_stream=False,
    )

    @classmethod
    def verify_webhook(
        cls, *, raw_body: bytes, headers: Mapping[str, str], secret: str, now: float
    ) -> str | None:
        # The same v0:ts:body HMAC scheme the Slack surface uses; the signature
        # carries a timestamp, so freshness (±5 min) is checked inside verify.
        valid = slack_signature.verify(
            secret,
            timestamp=headers.get("X-Slack-Request-Timestamp", ""),
            body=raw_body,
            signature=headers.get("X-Slack-Signature", ""),
            now=now,
        )
        return webhook_verify.body_fingerprint(raw_body) if valid else None

    def __init__(
        self,
        client: SourceHttpClient,
        *,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            scope_mode=scope_mode, scope_list=scope_list, content_filters=content_filters
        )
        self._client = client

    @classmethod
    def create(
        cls,
        *,
        base_url: str | None,
        credential: str,
        throttle: Throttle | None = None,
        scope_mode: str = "all",
        scope_list: tuple[str, ...] = (),
        content_filters: dict[str, object] | None = None,
    ) -> Self:
        del base_url  # SaaS endpoint is fixed (manifest: needs_base_url=False)
        client = SourceHttpClient(
            base_url=SLACK_API_BASE_URL,
            headers={"Authorization": f"Bearer {credential}"},
            throttle=throttle,
            request_cost=cls.manifest.request_cost,
        )
        return cls(
            client,
            scope_mode=scope_mode,
            scope_list=scope_list,
            content_filters=content_filters,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Web API helpers ---

    async def _call(
        self, method: str, params: dict[str, str | int] | None = None
    ) -> dict[str, object]:
        """One Web API call; unwraps the ok/error envelope (HTTP is 200 either way)."""
        payload = await self._client.get_json(f"/{method}", params=params)
        if payload.get("ok") is True:
            return payload
        error = str(payload.get("error") or "unknown_error")
        if error == _RATE_LIMITED_ERROR:
            raise SourceUnavailableError(f"{method}: {error}")
        raise SourceItemError(DlqReason.MALFORMED, f"{method}: {error}")

    async def _pages(
        self, method: str, params: dict[str, str | int]
    ) -> AsyncIterator[dict[str, object]]:
        """Cursor pagination: yield whole payloads until next_cursor comes back empty."""
        cursor = ""
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            payload = await self._call(method, page_params)
            yield payload
            cursor = _next_cursor(payload)
            if not cursor:
                return

    async def _paged(
        self, method: str, params: dict[str, str | int], *, item_key: str
    ) -> AsyncIterator[dict[str, object]]:
        """Item stream over _pages for the common "list under one key" shape."""
        async for payload in self._pages(method, params):
            for item in cast("list[dict[str, object]]", payload.get(item_key) or []):
                yield item

    def _channel_types(self) -> str:
        types = "public_channel"
        if self.content_filters.get(INCLUDE_PRIVATE_TOGGLE):
            types += ",private_channel"
        return types

    def _channels(self) -> AsyncIterator[dict[str, object]]:
        return self._paged(
            "conversations.list",
            {
                "types": self._channel_types(),
                "exclude_archived": "false",
                "limit": CHANNELS_PAGE_LIMIT,
            },
            item_key="channels",
        )

    # --- contract ---

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        async for channel in self._channels():
            channel_id = str(channel["id"])
            if not self.in_scope(channel_id):
                continue
            channel_ref: dict[str, object] = {
                "id": channel_id,
                "name": channel.get("name"),
                "is_private": channel.get("is_private", False),
            }
            async for message in self._history(channel_id, since):
                ts = str(message.get("ts"))
                replies: list[dict[str, object]] = []
                if message.get("thread_ts") == message.get("ts"):
                    try:
                        replies = await self._replies(channel_id, ts)
                    except SourceItemError as exc:
                        # Enrichment must not kill fetch(): the parent message
                        # still flows, just without its thread tail.
                        logger.warning(
                            "thread replies enrichment failed for %s:%s: %s",
                            channel_id,
                            ts,
                            exc.detail or exc,
                        )
                yield RawItem(
                    source_type="message",
                    source_entity_id=f"{channel_id}:{ts}",
                    payload={"channel": channel_ref, "message": message, "replies": replies},
                )

    async def _history(
        self, channel_id: str, since: datetime | None
    ) -> AsyncIterator[dict[str, object]]:
        """History pages arrive newest-first; each page is re-yielded in ascending ts."""
        params: dict[str, str | int] = {"channel": channel_id, "limit": HISTORY_PAGE_LIMIT}
        if since is not None:
            params["oldest"] = str(since.timestamp())
        async for payload in self._pages("conversations.history", params):
            messages = cast("list[dict[str, object]]", payload.get("messages") or [])
            for message in reversed(messages):
                if not _is_noise(message):
                    yield message

    async def _replies(self, channel_id: str, thread_ts: str) -> list[dict[str, object]]:
        """Thread tail without the parent (replies repeats it first), noise dropped."""
        return [
            message
            async for message in self._paged(
                "conversations.replies",
                {"channel": channel_id, "ts": thread_ts, "limit": HISTORY_PAGE_LIMIT},
                item_key="messages",
            )
            if str(message.get("ts")) != thread_ts and not _is_noise(message)
        ]

    def normalize(self, raw: RawItem) -> NormalizedEntity:
        channel = cast("dict[str, object]", raw.payload.get("channel") or {})
        message = cast("dict[str, object]", raw.payload.get("message") or {})
        replies = cast("list[dict[str, object]]", raw.payload.get("replies") or [])

        channel_id = str(channel.get("id"))
        # Private channel → grant on the channel container; public → workspace-open.
        if channel.get("is_private"):
            acl: tuple[AclNative, ...] = (AclNative(AclScope.GROUP, channel_id),)
        else:
            acl = (AclNative(AclScope.PUBLIC),)
        posted_at = _ts_to_datetime(message.get("ts"))
        user = message.get("user")
        return NormalizedEntity(
            source_type=raw.source_type,
            source_entity_id=raw.source_entity_id,
            title=None,
            body="\n\n".join(_render_line(m) for m in (message, *replies)) or None,
            url=None,
            status=EntityStatus.FINAL,
            author=PrincipalDraft(source_user_id=str(user)) if user else None,
            source_created_at=posted_at,
            source_updated_at=posted_at,
            acl=acl,
            links=(),
            meta={"channel_id": channel_id, "channel_name": channel.get("name")},
        )

    async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        users = self._paged("users.list", {"limit": USERS_PAGE_LIMIT}, item_key="members")
        async for user in users:
            user_id = str(user.get("id"))
            if user.get("deleted") or user.get("is_bot") or user_id == _BUILTIN_BOT_USER_ID:
                continue
            profile = cast("dict[str, object]", user.get("profile") or {})
            email = profile.get("email")
            display_name = user.get("real_name") or user.get("name")
            yield PrincipalDraft(
                source_user_id=user_id,
                email=str(email) if email else None,
                display_name=str(display_name) if display_name else None,
            )

    async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        async for channel in self._channels():
            channel_id = str(channel["id"])
            if not self.in_scope(channel_id):
                continue
            name = str(channel.get("name") or channel_id)
            if channel.get("is_private"):
                members = await self._member_ids(channel_id)
                yield GroupDraft(channel_id, name, "channel", tuple(members))
            else:
                # Public channel: access is workspace-wide, no membership snapshot.
                yield GroupDraft(channel_id, name, "channel")

    async def _member_ids(self, channel_id: str) -> list[str]:
        """conversations.members pages plain id strings, not objects."""
        ids: list[str] = []
        params: dict[str, str | int] = {"channel": channel_id, "limit": USERS_PAGE_LIMIT}
        async for payload in self._pages("conversations.members", params):
            ids.extend(str(m) for m in cast("list[object]", payload.get("members") or []))
        return ids

    async def list_catalog(self) -> list[ScopeObject]:
        return [
            ScopeObject(
                native_id=str(channel["id"]),
                name=str(channel.get("name") or channel["id"]),
                kind="channel",
            )
            async for channel in self._channels()
        ]

    async def check_connection(self) -> Diagnosis:
        """Stepped probe: auth.test proves reach + token, conversations.list — scope."""
        try:
            await self._call("auth.test")
        except SourceUnavailableError as exc:
            return build_diagnosis("reachability", str(exc))
        except SourceItemError as exc:
            return build_diagnosis("credentials", exc.detail or str(exc))
        try:
            await self._call("conversations.list", {"limit": 1, "types": self._channel_types()})
        except (SourceItemError, SourceUnavailableError) as exc:
            return build_diagnosis("permissions", str(exc))
        return build_diagnosis()
