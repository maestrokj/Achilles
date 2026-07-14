"""GitLab connector: issues + merge requests per project (connectors.html#gitlab).

Auth is a personal/group access token in the PRIVATE-TOKEN header. GitLab
signals throttling with 403 + RateLimit-Remaining rather than only 429, hence
the classification override (reliability.html#classification). Discussion
notes are folded into the item body at fetch time — one RawItem per issue/MR.
"""

import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Self, cast

import httpx

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
    classify_error,
)
from achilles.harvester.connectors.registry import register_connector
from achilles.harvester.constants import ErrorClass, RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier, EntityStatus

logger = logging.getLogger(__name__)

API_PATH = "/api/v4"
PAGE_SIZE = 50
NOTES_PAGE_SIZE = 100
MEMBERS_PAGE_SIZE = 100

# (source_type, REST collection) pairs harvested per project.
_ITEM_KINDS = (("issue", "issues"), ("merge_request", "merge_requests"))

_STATE_TO_STATUS: dict[str, EntityStatus] = {
    "opened": EntityStatus.DRAFT,
    "closed": EntityStatus.FINAL,
    "merged": EntityStatus.FINAL,
}

# GitLab project visibility → KS grant. public/internal are org-wide readable
# (the KS has no anonymous reader, so PUBLIC means every authenticated user);
# private stays scoped to the project's own member group.
_ORG_WIDE_VISIBILITY = frozenset({"public", "internal"})


def _classify(status_code: int, headers: httpx.Headers) -> ErrorClass:
    """GitLab rate limiting answers 403 with RateLimit-Remaining → retry, not DLQ."""
    if status_code == 403 and "RateLimit-Remaining" in headers:
        return ErrorClass.TRANSIENT
    return classify_error(status_code, headers)


def _parse_ts(value: object) -> datetime | None:
    """GitLab ISO 8601 timestamp → aware UTC datetime."""
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _principal(user: dict[str, object]) -> PrincipalDraft | None:
    """GitLab user object → draft; email is usually hidden (public_email opt-in)."""
    user_id = user.get("id")
    if user_id is None:
        return None
    email = user.get("public_email")
    display_name = user.get("name") or user.get("username")
    return PrincipalDraft(
        source_user_id=str(user_id),
        email=str(email) if email else None,
        display_name=str(display_name) if display_name else None,
    )


@register_connector
class GitLabConnector(BaseConnector):
    """Membership projects → issues + merge requests, notes in the body."""

    manifest = ConnectorManifest(
        type="gitlab",
        title="GitLab",
        needs_base_url=True,
        credential_label="Personal/Group access token",
        scope_kinds=("project",),
        incremental=True,
        webhooks=True,
        rate_limit_per_second=1.5,
        rate_limit_scope=RateLimitScope.ACCOUNT_TOKEN,
        default_authority=AuthorityTier.NORMAL,
        # Per-project x per-collection streams each restart ascending order —
        # the whole fetch is not globally ordered by source_updated_at.
        ordered_stream=False,
    )

    @classmethod
    def verify_webhook(
        cls, *, raw_body: bytes, headers: Mapping[str, str], secret: str, now: float
    ) -> str | None:
        # GitLab does not sign — it echoes the configured secret token verbatim
        # in X-Gitlab-Token; the event UUID is the delivery id.
        del now
        presented = headers.get("X-Gitlab-Token", "")
        if not presented or not webhook_verify.token_matches(secret, presented):
            return None
        return headers.get("X-Gitlab-Event-UUID") or webhook_verify.body_fingerprint(raw_body)

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
        # One instance per run: fetch_principals + fetch_groups share one member sweep.
        self._members_cache: dict[str, list[dict[str, object]]] = {}

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
        client = SourceHttpClient(
            base_url=(base_url or "").rstrip("/") + API_PATH,
            headers={"PRIVATE-TOKEN": credential},
            throttle=throttle,
            classify=_classify,
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

    # --- shared REST helpers ---

    async def _paged(
        self, url: str, params: dict[str, str | int], *, per_page: int = PAGE_SIZE
    ) -> AsyncIterator[dict[str, object]]:
        """Offset pagination: a short page means the collection is exhausted."""
        page = 1
        while True:
            items = await self._client.get_json_list(
                url, params={**params, "per_page": per_page, "page": page}
            )
            for item in items:
                yield item
            if len(items) < per_page:
                return
            page += 1

    async def _projects(self) -> AsyncIterator[dict[str, object]]:
        """Membership projects the admin kept in scope."""
        async for project in self._paged("/projects", {"membership": "true"}):
            if self.in_scope(str(project["id"])):
                yield project

    async def _members(self, project_id: str) -> list[dict[str, object]]:
        """Direct + inherited members (members/all), swept once per run per project."""
        cached = self._members_cache.get(project_id)
        if cached is None:
            cached = [
                member
                async for member in self._paged(
                    f"/projects/{project_id}/members/all", {}, per_page=MEMBERS_PAGE_SIZE
                )
            ]
            self._members_cache[project_id] = cached
        return cached

    # --- contract ---

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        params: dict[str, str | int] = {"order_by": "updated_at", "sort": "asc"}
        if since is not None:
            params["updated_after"] = since.isoformat()
        async for project in self._projects():
            project_id = str(project["id"])
            project_ref: dict[str, object] = {
                "id": project["id"],
                "path_with_namespace": project.get("path_with_namespace"),
                "visibility": project.get("visibility"),
            }
            for source_type, collection in _ITEM_KINDS:
                async for item in self._paged(f"/projects/{project_id}/{collection}", params):
                    # List payloads carry user_notes_count — skip the notes call at 0.
                    notes: list[dict[str, object]] = []
                    if item.get("user_notes_count"):
                        try:
                            notes = await self._notes(project_id, collection, item)
                        except SourceItemError as exc:
                            # Enrichment must not kill fetch(): the item still
                            # flows, just without its discussion notes.
                            logger.warning(
                                "notes enrichment failed for %s %s/%s: %s",
                                source_type,
                                project_id,
                                item.get("iid"),
                                exc.detail or exc,
                            )
                    yield RawItem(
                        source_type=source_type,
                        source_entity_id=str(item["id"]),
                        payload={**item, "_notes": notes, "_project": project_ref},
                    )

    async def _notes(
        self, project_id: str, collection: str, item: dict[str, object]
    ) -> list[dict[str, object]]:
        """Human discussion only — system notes are workflow noise, dropped here."""
        url = f"/projects/{project_id}/{collection}/{item['iid']}/notes"
        return [
            note
            async for note in self._paged(url, {}, per_page=NOTES_PAGE_SIZE)
            if note.get("system") is not True
        ]

    def normalize(self, raw: RawItem) -> NormalizedEntity:
        payload = cast("dict[str, object]", raw.payload)
        project = cast("dict[str, object]", payload.get("_project") or {})
        notes = cast("list[dict[str, object]]", payload.get("_notes") or [])

        parts: list[str] = []
        description = payload.get("description")
        if description:
            parts.append(str(description))
        for note in notes:
            note_author = cast("dict[str, object]", note.get("author") or {})
            speaker = note_author.get("name") or note_author.get("username") or "unknown"
            parts.append(f"{speaker}: {note.get('body') or ''}")

        visibility = project.get("visibility")
        if isinstance(visibility, str) and visibility in _ORG_WIDE_VISIBILITY:
            # A public/internal project's items are readable org-wide.
            acl: tuple[AclNative, ...] = (AclNative(AclScope.PUBLIC),)
        else:
            # Private project: visibility maps to a group grant on the container.
            acl = (AclNative(AclScope.GROUP, str(project.get("id"))),)

        state = payload.get("state")
        title = payload.get("title")
        url = payload.get("web_url")
        return NormalizedEntity(
            source_type=raw.source_type,
            source_entity_id=raw.source_entity_id,
            title=str(title) if title else None,
            body="\n\n".join(parts) or None,
            url=str(url) if url else None,
            status=_STATE_TO_STATUS.get(state) if isinstance(state, str) else None,
            author=_principal(cast("dict[str, object]", payload.get("author") or {})),
            source_created_at=_parse_ts(payload.get("created_at")),
            source_updated_at=_parse_ts(payload.get("updated_at")),
            acl=acl,
            # v1: cross-references stay in the text; Curation mines mentions (v2).
            links=(),
            meta={
                "project": project.get("path_with_namespace"),
                "labels": payload.get("labels") or [],
            },
        )

    async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        seen: set[str] = set()
        async for project in self._projects():
            for member in await self._members(str(project["id"])):
                draft = _principal(member)
                if draft is None or draft.source_user_id in seen:
                    continue
                seen.add(draft.source_user_id)
                yield draft

    async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        async for project in self._projects():
            project_id = str(project["id"])
            member_ids = [str(member["id"]) for member in await self._members(project_id)]
            yield GroupDraft(
                source_group_id=project_id,
                name=str(project.get("path_with_namespace") or project_id),
                kind="project",
                member_source_user_ids=tuple(member_ids),
            )

    async def list_catalog(self) -> list[ScopeObject]:
        return [
            ScopeObject(
                native_id=str(project["id"]),
                name=str(project.get("path_with_namespace") or project["id"]),
                kind="project",
            )
            async for project in self._paged("/projects", {"membership": "true"})
        ]

    async def check_connection(self) -> Diagnosis:
        """Stepped probe: /user proves reach + token, /projects proves read scope."""
        try:
            await self._client.get_json("/user")
        except SourceUnavailableError as exc:
            return build_diagnosis("reachability", str(exc))
        except SourceItemError as exc:
            return build_diagnosis("credentials", exc.detail or str(exc))
        try:
            await self._client.get_json_list(
                "/projects", params={"membership": "true", "per_page": 1}
            )
        except (SourceItemError, SourceUnavailableError) as exc:
            return build_diagnosis("permissions", str(exc))
        return build_diagnosis()
