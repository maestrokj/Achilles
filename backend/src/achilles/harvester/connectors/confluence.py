"""Confluence Cloud connector (connectors.html#confluence): pages + blog posts via CQL."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from achilles.harvester.connectors._atlassian import (
    SINCE_TZ_SKEW,
    AtlassianConnector,
    as_dict,
    as_list,
    as_str,
    parse_atlassian_datetime,
    strip_markup,
)
from achilles.harvester.connectors.base import (
    AclNative,
    ConnectorManifest,
    DiagnosisStep,
    GroupDraft,
    LinkDraft,
    NormalizedEntity,
    PrincipalDraft,
    RawItem,
    ScopeObject,
)
from achilles.harvester.connectors.http import SourceItemError, SourceUnavailableError
from achilles.harvester.connectors.registry import register_connector
from achilles.harvester.constants import RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier, EntityStatus

PAGE_SIZE = 25

_CONTENT_SEARCH_PATH = "/rest/api/content/search"
_SPACES_PATH = "/rest/api/space"
_EXPAND = "body.storage,version,history,space,ancestors"
_CQL_SINCE_FORMAT = "%Y/%m/%d %H:%M"
_READ_OPERATION = "read"

_STATUS_MAP: dict[str, EntityStatus] = {
    "current": EntityStatus.FINAL,
    "draft": EntityStatus.DRAFT,
    "trashed": EntityStatus.ARCHIVED,
    "archived": EntityStatus.ARCHIVED,
}


@register_connector
class ConfluenceConnector(AtlassianConnector):
    """Documentation source — curated pages carry HIGH default authority."""

    manifest = ConnectorManifest(
        type="confluence",
        title="Confluence",
        needs_base_url=True,
        credential_label="email:api_token",
        scope_kinds=("space",),
        incremental=True,
        webhooks=False,
        rate_limit_per_second=2.0,
        rate_limit_scope=RateLimitScope.TENANT,
        default_authority=AuthorityTier.HIGH,
    )

    identity_probe_path = "/rest/api/user/current"

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        cql = self._build_cql(since)
        start = 0
        while True:
            payload = await self.client.get_json(
                _CONTENT_SEARCH_PATH,
                params={"cql": cql, "expand": _EXPAND, "start": start, "limit": PAGE_SIZE},
            )
            results = as_list(payload.get("results"))
            for item in results:
                page = as_dict(item)
                page_id = page.get("id")
                if page_id is None:
                    continue
                # CQL has no deny-list form — space scope is filtered client-side.
                space_key = as_str(as_dict(page.get("space")).get("key"))
                if space_key is not None and not self.in_scope(space_key):
                    continue
                yield RawItem(source_type="page", source_entity_id=str(page_id), payload=page)
            if len(results) < PAGE_SIZE:
                break
            start += len(results)

    def _build_cql(self, since: datetime | None) -> str:
        cql = "type IN (page,blogpost)"
        if since is not None:
            # The literal is read in the instance timezone — back it off by
            # SINCE_TZ_SKEW so no zone can push the threshold past the watermark.
            stamp = (since - SINCE_TZ_SKEW).astimezone(UTC).strftime(_CQL_SINCE_FORMAT)
            cql += f' AND lastmodified >= "{stamp}"'
        return f"{cql} ORDER BY lastmodified ASC"

    def normalize(self, raw: RawItem) -> NormalizedEntity:
        payload: dict[str, object] = raw.payload
        space_key = as_str(as_dict(payload.get("space")).get("key"))

        history = as_dict(payload.get("history"))
        created_by = as_dict(history.get("createdBy"))
        author_id = as_str(created_by.get("accountId"))
        author = (
            PrincipalDraft(
                source_user_id=author_id,
                email=as_str(created_by.get("email")),
                display_name=as_str(created_by.get("displayName")),
            )
            if author_id
            else None
        )

        storage_value = as_str(as_dict(as_dict(payload.get("body")).get("storage")).get("value"))
        body = (strip_markup(storage_value) or None) if storage_value else None

        webui = as_str(as_dict(payload.get("_links")).get("webui"))
        created = as_str(history.get("createdDate"))
        updated = as_str(as_dict(payload.get("version")).get("when"))

        # Ancestors come root-first; the last one is the direct parent.
        ancestors = as_list(payload.get("ancestors"))
        links: tuple[LinkDraft, ...] = ()
        if ancestors:
            parent_id = as_dict(ancestors[-1]).get("id")
            if parent_id is not None:
                links = (
                    LinkDraft(relation="child_of", target_kind="page", target_ref=str(parent_id)),
                )

        return NormalizedEntity(
            source_type=raw.source_type,
            source_entity_id=raw.source_entity_id,
            title=as_str(payload.get("title")),
            body=body,
            url=f"{self.base_url}{webui}" if webui else None,
            status=_STATUS_MAP.get(as_str(payload.get("status")) or ""),
            author=author,
            source_created_at=parse_atlassian_datetime(created) if created else None,
            source_updated_at=parse_atlassian_datetime(updated) if updated else None,
            acl=(AclNative(AclScope.GROUP, space_key),) if space_key else (),
            links=links,
            meta={"space": space_key} if space_key else {},
        )

    async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        """No principal directory in v1 — authors arrive embedded in content."""
        return
        yield  # pragma: no cover

    async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        async for space in self._iter_spaces():
            key = as_str(space.get("key"))
            if key is None or not self.in_scope(key):
                continue
            yield GroupDraft(
                source_group_id=key,
                name=as_str(space.get("name")) or key,
                kind="space",
                member_source_user_ids=await self._space_reader_ids(key),
            )

    async def _iter_spaces(self) -> AsyncIterator[dict[str, object]]:
        start = 0
        while True:
            payload = await self.client.get_json(
                _SPACES_PATH, params={"start": start, "limit": PAGE_SIZE}
            )
            results = as_list(payload.get("results"))
            for item in results:
                yield as_dict(item)
            if len(results) < PAGE_SIZE:
                break
            start += len(results)

    async def _space_reader_ids(self, key: str) -> tuple[str, ...]:
        """Best-effort membership snapshot (acl-identity.html#acl).

        The Cloud space-permission API is unstable across deployments; when it
        answers with a permanent error the group keeps an empty membership
        instead of failing the sync — v1 accepts the coarser ACL.
        """
        try:
            payload = await self.client.get_json(f"{_SPACES_PATH}/{key}/permission")
        except SourceItemError:
            return ()
        ids: list[str] = []
        for item in as_list(payload.get("results")):
            entry = as_dict(item)
            operation = as_dict(entry.get("operation"))
            if _READ_OPERATION not in (operation.get("key"), operation.get("operation")):
                continue
            users = as_list(as_dict(as_dict(entry.get("subjects")).get("user")).get("results"))
            ids.extend(
                account_id
                for user in users
                if (account_id := as_str(as_dict(user).get("accountId"))) is not None
            )
        return tuple(dict.fromkeys(ids))

    async def list_catalog(self) -> list[ScopeObject]:
        return [
            ScopeObject(native_id=key, name=as_str(space.get("name")) or key, kind="space")
            async for space in self._iter_spaces()
            if (key := as_str(space.get("key"))) is not None
        ]

    async def _probe_permissions(self) -> DiagnosisStep:
        try:
            payload = await self.client.get_json(_SPACES_PATH, params={"limit": 1})
        except (SourceItemError, SourceUnavailableError) as exc:
            return DiagnosisStep(name="permissions", ok=False, detail=str(exc))
        if not as_list(payload.get("results")):
            return DiagnosisStep(name="permissions", ok=False, detail="no visible spaces")
        return DiagnosisStep(name="permissions", ok=True)
