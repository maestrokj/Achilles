"""Jira Cloud connector (connectors.html#jira): issues via REST API v2 search."""

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from achilles.harvester.connectors import webhook_verify
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

PAGE_SIZE = 50

_SEARCH_PATH = "/rest/api/2/search"
_PROJECTS_PATH = "/rest/api/2/project"
_USERS_SEARCH_PATH = "/rest/api/2/users/search"
_ASSIGNABLE_SEARCH_PATH = "/rest/api/2/user/assignable/search"

# `labels` rides along the spec'd field list so meta.labels can be populated.
_ISSUE_FIELDS = (
    "summary,description,comment,status,reporter,created,updated,issuelinks,project,labels"
)

# Human accounts; "app"/"customer" account types never become identities.
_HUMAN_ACCOUNT_TYPE = "atlassian"
_DONE_CATEGORY = "done"
_JQL_SINCE_FORMAT = "%Y-%m-%d %H:%M"


@register_connector
class JiraConnector(AtlassianConnector):
    """Issues in, one project = one ACL group (acl-identity.html#acl)."""

    manifest = ConnectorManifest(
        type="jira",
        title="Jira",
        needs_base_url=True,
        credential_label="email:api_token",
        scope_kinds=("project",),
        incremental=True,
        webhooks=True,
        rate_limit_per_second=2.0,
        rate_limit_scope=RateLimitScope.TENANT,
        default_authority=AuthorityTier.NORMAL,
    )

    identity_probe_path = "/rest/api/2/myself"

    @classmethod
    def verify_webhook(
        cls, *, raw_body: bytes, headers: Mapping[str, str], secret: str, now: float
    ) -> str | None:
        # Jira signs the body with HMAC-SHA256 in X-Hub-Signature (sha256=…);
        # deliveries carry no timestamp, so replay is held by dedup alone.
        del now
        presented = headers.get("X-Hub-Signature", "")
        if not presented or not webhook_verify.signature_matches(
            secret, raw_body, presented, prefix="sha256="
        ):
            return None
        return headers.get("X-Atlassian-Webhook-Identifier") or webhook_verify.body_fingerprint(
            raw_body
        )

    async def fetch(self, since: datetime | None) -> AsyncIterator[RawItem]:
        jql = self._build_jql(since)
        start_at = 0
        while True:
            payload = await self.client.get_json(
                _SEARCH_PATH,
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": PAGE_SIZE,
                    "fields": _ISSUE_FIELDS,
                },
            )
            issues = as_list(payload.get("issues"))
            for item in issues:
                issue = as_dict(item)
                key = as_str(issue.get("key"))
                if key is None:
                    continue
                # Deny-list scope is not expressible in JQL — filter client-side.
                project = as_dict(as_dict(issue.get("fields")).get("project"))
                project_key = as_str(project.get("key"))
                if project_key is not None and not self.in_scope(project_key):
                    continue
                yield RawItem(source_type="issue", source_entity_id=key, payload=issue)
            start_at += len(issues)
            total = payload.get("total")
            if not issues or (isinstance(total, int) and start_at >= total):
                break

    def _build_jql(self, since: datetime | None) -> str:
        clauses: list[str] = []
        if self.scope_mode == "selected" and self.scope_list:
            keys = ", ".join(f'"{key}"' for key in self.scope_list)
            clauses.append(f"project IN ({keys})")
        if since is not None:
            # The literal is read in the API user's timezone — back it off by
            # SINCE_TZ_SKEW so no zone can push the threshold past the watermark.
            stamp = (since - SINCE_TZ_SKEW).astimezone(UTC).strftime(_JQL_SINCE_FORMAT)
            clauses.append(f'updated >= "{stamp}"')
        order = "ORDER BY updated ASC"
        return f"{' AND '.join(clauses)} {order}" if clauses else order

    def normalize(self, raw: RawItem) -> NormalizedEntity:
        payload: dict[str, object] = raw.payload
        fields = as_dict(payload.get("fields"))
        project_key = as_str(as_dict(fields.get("project")).get("key"))

        parts: list[str] = []
        description = as_str(fields.get("description"))
        if description:
            text = strip_markup(description)
            if text:
                parts.append(text)
        for item in as_list(as_dict(fields.get("comment")).get("comments")):
            comment = as_dict(item)
            text = strip_markup(as_str(comment.get("body")) or "")
            if not text:
                continue
            author_name = as_str(as_dict(comment.get("author")).get("displayName")) or "unknown"
            parts.append(f"{author_name}: {text}")

        category_key = as_dict(as_dict(fields.get("status")).get("statusCategory")).get("key")
        status = EntityStatus.FINAL if category_key == _DONE_CATEGORY else EntityStatus.DRAFT

        reporter = as_dict(fields.get("reporter"))
        reporter_id = as_str(reporter.get("accountId"))
        author = (
            PrincipalDraft(
                source_user_id=reporter_id,
                email=as_str(reporter.get("emailAddress")),
                display_name=as_str(reporter.get("displayName")),
            )
            if reporter_id
            else None
        )

        links: list[LinkDraft] = []
        for item in as_list(fields.get("issuelinks")):
            link = as_dict(item)
            relation = (as_str(as_dict(link.get("type")).get("name")) or "links_to").lower()
            for side in ("outwardIssue", "inwardIssue"):
                target_key = as_str(as_dict(link.get(side)).get("key"))
                if target_key:
                    links.append(
                        LinkDraft(relation=relation, target_kind="issue", target_ref=target_key)
                    )

        meta: dict[str, object] = {}
        if project_key:
            meta["project"] = project_key
        labels = [label for label in as_list(fields.get("labels")) if isinstance(label, str)]
        if labels:
            meta["labels"] = labels

        created = as_str(fields.get("created"))
        updated = as_str(fields.get("updated"))
        return NormalizedEntity(
            source_type=raw.source_type,
            source_entity_id=raw.source_entity_id,
            title=as_str(fields.get("summary")),
            body="\n\n".join(parts) or None,
            url=f"{self.base_url}/browse/{raw.source_entity_id}",
            status=status,
            author=author,
            source_created_at=parse_atlassian_datetime(created) if created else None,
            source_updated_at=parse_atlassian_datetime(updated) if updated else None,
            acl=(AclNative(AclScope.GROUP, project_key),) if project_key else (),
            links=tuple(links),
            meta=meta,
        )

    async def fetch_principals(self) -> AsyncIterator[PrincipalDraft]:
        start_at = 0
        while True:
            users = await self.client.get_json_list(
                _USERS_SEARCH_PATH, params={"startAt": start_at, "maxResults": PAGE_SIZE}
            )
            for user in users:
                if user.get("accountType") != _HUMAN_ACCOUNT_TYPE:
                    continue
                account_id = as_str(user.get("accountId"))
                if account_id is None:
                    continue
                yield PrincipalDraft(
                    source_user_id=account_id,
                    email=as_str(user.get("emailAddress")),
                    display_name=as_str(user.get("displayName")),
                )
            if len(users) < PAGE_SIZE:
                break
            start_at += len(users)

    async def fetch_groups(self) -> AsyncIterator[GroupDraft]:
        projects = await self.client.get_json_list(_PROJECTS_PATH)
        for project in projects:
            key = as_str(project.get("key"))
            if key is None or not self.in_scope(key):
                continue
            yield GroupDraft(
                source_group_id=key,
                name=as_str(project.get("name")) or key,
                kind="project",
                member_source_user_ids=await self._assignable_account_ids(key),
            )

    async def _assignable_account_ids(self, project_key: str) -> tuple[str, ...]:
        """Project membership approximated by assignable users (acl-identity.html#acl)."""
        ids: list[str] = []
        start_at = 0
        while True:
            users = await self.client.get_json_list(
                _ASSIGNABLE_SEARCH_PATH,
                params={"project": project_key, "startAt": start_at, "maxResults": PAGE_SIZE},
            )
            ids.extend(
                account_id
                for user in users
                if (account_id := as_str(user.get("accountId"))) is not None
            )
            if len(users) < PAGE_SIZE:
                break
            start_at += len(users)
        return tuple(dict.fromkeys(ids))

    async def list_catalog(self) -> list[ScopeObject]:
        projects = await self.client.get_json_list(_PROJECTS_PATH)
        return [
            ScopeObject(native_id=key, name=as_str(project.get("name")) or key, kind="project")
            for project in projects
            if (key := as_str(project.get("key"))) is not None
        ]

    async def _probe_permissions(self) -> DiagnosisStep:
        try:
            projects = await self.client.get_json_list(_PROJECTS_PATH)
        except (SourceItemError, SourceUnavailableError) as exc:
            return DiagnosisStep(name="permissions", ok=False, detail=str(exc))
        if not projects:
            return DiagnosisStep(name="permissions", ok=False, detail="no visible projects")
        return DiagnosisStep(name="permissions", ok=True)
