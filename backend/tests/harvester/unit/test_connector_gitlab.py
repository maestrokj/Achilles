"""GitLab connector: fetch, normalize, catalog, diagnosis (unit, respx)."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
import respx

from achilles.harvester.connectors import http as http_core
from achilles.harvester.connectors.base import AclNative, PrincipalDraft, RawItem
from achilles.harvester.connectors.gitlab import GitLabConnector
from achilles.harvester.constants import RateLimitScope
from achilles.knowledge_store.constants import AclScope, AuthorityTier, EntityStatus

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://gitlab.test"
API = f"{BASE}/api/v4"

PROJECT = {"id": 1, "path_with_namespace": "team/app"}

ISSUE = {
    "id": 101,
    "iid": 7,
    "title": "Login broken",
    "description": "Steps to reproduce",
    "state": "opened",
    "web_url": f"{BASE}/team/app/-/issues/7",
    "created_at": "2026-05-01T10:00:00.000Z",
    "updated_at": "2026-06-01T12:30:00.000Z",
    "labels": ["bug"],
    "author": {"id": 5, "username": "alice", "name": "Alice", "public_email": ""},
    "user_notes_count": 2,
}

NOTES = [
    {"id": 11, "system": False, "body": "Looking into it", "author": {"id": 6, "name": "Bob"}},
    {"id": 12, "system": True, "body": "changed milestone", "author": {"id": 5, "name": "Alice"}},
]


def _zero_backoff(_attempt: int) -> float:
    return 0.0


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_core, "_backoff_seconds", _zero_backoff)


def _connector(*, scope_mode: str = "all", scope_list: tuple[str, ...] = ()) -> GitLabConnector:
    return GitLabConnector.create(
        base_url=BASE,
        credential="secret",
        scope_mode=scope_mode,
        scope_list=scope_list,
    )


def _raw_issue(**overrides: object) -> RawItem:
    payload: dict[str, object] = {**ISSUE, "_notes": [NOTES[0]], "_project": PROJECT}
    payload.update(overrides)
    return RawItem(source_type="issue", source_entity_id="101", payload=payload)


def test_manifest() -> None:
    manifest = GitLabConnector.manifest
    assert manifest.type == "gitlab"
    assert manifest.needs_base_url
    assert manifest.scope_kinds == ("project",)
    assert manifest.webhooks
    assert manifest.rate_limit_scope == RateLimitScope.ACCOUNT_TOKEN
    assert manifest.default_authority == AuthorityTier.NORMAL
    assert not manifest.ordered_stream  # per-project x per-collection streams


@respx.mock
async def test_fetch_paginates_issues_and_passes_updated_after() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    full_page = [{"id": 1000 + i, "iid": i, "user_notes_count": 1} for i in range(50)]
    issues_route = respx.get(f"{API}/projects/1/issues").mock(
        side_effect=[
            httpx.Response(200, json=full_page),
            httpx.Response(200, json=[{"id": 2000, "iid": 99, "user_notes_count": 1}]),
        ]
    )
    respx.get(host="gitlab.test", path__regex=r"/api/v4/projects/1/issues/\d+/notes").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{API}/projects/1/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector()
    since = datetime(2026, 6, 1, tzinfo=UTC)
    items = [item async for item in connector.fetch(since)]
    await connector.aclose()

    assert len(items) == 51
    assert issues_route.call_count == 2
    first_params = issues_route.calls[0].request.url.params
    assert first_params["updated_after"] == since.isoformat()
    assert first_params["order_by"] == "updated_at"
    assert first_params["sort"] == "asc"
    assert issues_route.calls[1].request.url.params["page"] == "2"


@respx.mock
async def test_fetch_full_sync_has_no_updated_after() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    issues_route = respx.get(f"{API}/projects/1/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{API}/projects/1/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert items == []
    assert "updated_after" not in issues_route.calls[0].request.url.params


@respx.mock
async def test_fetch_folds_notes_and_drops_system_ones() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    respx.get(f"{API}/projects/1/issues").mock(return_value=httpx.Response(200, json=[ISSUE]))
    respx.get(f"{API}/projects/1/issues/7/notes").mock(return_value=httpx.Response(200, json=NOTES))
    respx.get(f"{API}/projects/1/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    (item,) = items
    assert item.source_type == "issue"
    assert item.source_entity_id == "101"
    assert [note["id"] for note in item.payload["_notes"]] == [11]  # system note dropped
    assert item.payload["_project"] == {
        "id": 1,
        "path_with_namespace": "team/app",
        "visibility": None,
    }


@respx.mock
async def test_fetch_skips_notes_call_when_no_comments() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    quiet = {"id": 300, "iid": 30, "user_notes_count": 0}
    noisy = {"id": 301, "iid": 31, "user_notes_count": 2}
    respx.get(f"{API}/projects/1/issues").mock(
        return_value=httpx.Response(200, json=[quiet, noisy])
    )
    # Only issue 31 has a notes route — a call for issue 30 would fail as unmocked.
    notes_route = respx.get(f"{API}/projects/1/issues/31/notes").mock(
        return_value=httpx.Response(200, json=[NOTES[0]])
    )
    respx.get(f"{API}/projects/1/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert notes_route.call_count == 1
    quiet_item, noisy_item = items
    assert quiet_item.payload["_notes"] == []
    assert [note["id"] for note in noisy_item.payload["_notes"]] == [11]


@respx.mock
async def test_fetch_survives_permanent_notes_failure() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    gone = {"id": 400, "iid": 40, "user_notes_count": 1}
    alive = {"id": 401, "iid": 41, "user_notes_count": 1}
    respx.get(f"{API}/projects/1/issues").mock(return_value=httpx.Response(200, json=[gone, alive]))
    # Issue 40 was just deleted — its notes call answers 404 (permanent).
    respx.get(f"{API}/projects/1/issues/40/notes").mock(return_value=httpx.Response(404))
    respx.get(f"{API}/projects/1/issues/41/notes").mock(
        return_value=httpx.Response(200, json=[NOTES[0]])
    )
    respx.get(f"{API}/projects/1/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    # Both items still flow; the failed enrichment only degrades its notes.
    gone_item, alive_item = items
    assert gone_item.source_entity_id == "400"
    assert gone_item.payload["_notes"] == []
    assert [note["id"] for note in alive_item.payload["_notes"]] == [11]


@respx.mock
async def test_fetch_scope_selected_is_an_allow_list() -> None:
    projects = [PROJECT, {"id": 2, "path_with_namespace": "team/other"}]
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=projects))
    # Only project 2 routes exist — a call to project 1 would fail as unmocked.
    respx.get(f"{API}/projects/2/issues").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/projects/2/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector(scope_mode="selected", scope_list=("2",))
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert items == []


@respx.mock
async def test_fetch_scope_all_is_a_deny_list() -> None:
    projects = [PROJECT, {"id": 2, "path_with_namespace": "team/other"}]
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=projects))
    respx.get(f"{API}/projects/2/issues").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/projects/2/merge_requests").mock(return_value=httpx.Response(200, json=[]))

    connector = _connector(scope_list=("1",))
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert items == []


async def test_normalize_issue_fields() -> None:
    connector = _connector()
    entity = connector.normalize(_raw_issue())
    await connector.aclose()

    assert entity.source_type == "issue"
    assert entity.source_entity_id == "101"
    assert entity.title == "Login broken"
    assert entity.body == "Steps to reproduce\n\nBob: Looking into it"
    assert entity.url == f"{BASE}/team/app/-/issues/7"
    assert entity.status == EntityStatus.DRAFT
    assert entity.author == PrincipalDraft("5", None, "Alice")  # empty public_email → None
    assert entity.source_created_at == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    assert entity.source_updated_at == datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
    assert entity.acl == (AclNative(AclScope.GROUP, "1"),)  # no visibility → private → group
    assert entity.links == ()
    assert entity.meta == {"project": "team/app", "labels": ["bug"]}


async def test_normalize_acl_follows_project_visibility() -> None:
    connector = _connector()
    for visibility in ("public", "internal"):
        project = {**PROJECT, "visibility": visibility}
        entity = connector.normalize(_raw_issue(_project=project))
        assert entity.acl == (AclNative(AclScope.PUBLIC),), visibility
    private = {**PROJECT, "visibility": "private"}
    assert connector.normalize(_raw_issue(_project=private)).acl == (
        AclNative(AclScope.GROUP, "1"),
    )
    await connector.aclose()


async def test_normalize_state_maps_to_status() -> None:
    connector = _connector()
    assert connector.normalize(_raw_issue(state="opened")).status == EntityStatus.DRAFT
    assert connector.normalize(_raw_issue(state="closed")).status == EntityStatus.FINAL
    assert connector.normalize(_raw_issue(state="merged")).status == EntityStatus.FINAL
    assert connector.normalize(_raw_issue(state="weird")).status is None
    await connector.aclose()


@respx.mock
async def test_fetch_principals_paginates_and_dedupes() -> None:
    projects = [PROJECT, {"id": 2, "path_with_namespace": "team/other"}]
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=projects))
    full_page = [{"id": i, "name": f"User {i}"} for i in range(100)]
    members_route = respx.get(f"{API}/projects/1/members/all").mock(
        side_effect=[
            httpx.Response(200, json=full_page),
            httpx.Response(200, json=[{"id": 100, "name": "Tail", "public_email": "t@x.test"}]),
        ]
    )
    respx.get(f"{API}/projects/2/members/all").mock(
        return_value=httpx.Response(200, json=[{"id": 0, "name": "User 0"}])
    )

    connector = _connector()
    principals = [principal async for principal in connector.fetch_principals()]
    await connector.aclose()

    assert members_route.call_count == 2
    assert len(principals) == 101  # id 0 from project 2 deduplicated
    assert principals[-1] == PrincipalDraft("100", "t@x.test", "Tail")


@respx.mock
async def test_fetch_groups_snapshots_membership() -> None:
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[PROJECT]))
    respx.get(f"{API}/projects/1/members/all").mock(
        return_value=httpx.Response(
            200, json=[{"id": 5, "name": "Alice"}, {"id": 6, "name": "Bob"}]
        )
    )

    connector = _connector()
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    (group,) = groups
    assert group.source_group_id == "1"
    assert group.name == "team/app"
    assert group.kind == "project"
    assert group.member_source_user_ids == ("5", "6")


@respx.mock
async def test_membership_sweep_is_cached_across_principals_and_groups() -> None:
    projects_route = respx.get(f"{API}/projects").mock(
        return_value=httpx.Response(200, json=[PROJECT])
    )
    members_route = respx.get(f"{API}/projects/1/members/all").mock(
        return_value=httpx.Response(200, json=[{"id": 5, "name": "Alice"}])
    )

    connector = _connector()
    principals = [principal async for principal in connector.fetch_principals()]
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    assert [principal.source_user_id for principal in principals] == ["5"]
    assert groups[0].member_source_user_ids == ("5",)
    assert members_route.call_count == 1  # second sweep reuses the cached payloads
    assert projects_route.call_count == 2


@respx.mock
async def test_list_catalog() -> None:
    projects = [PROJECT, {"id": 2, "path_with_namespace": "team/other"}]
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=projects))

    connector = _connector()
    catalog = await connector.list_catalog()
    await connector.aclose()

    assert [(obj.native_id, obj.name, obj.kind) for obj in catalog] == [
        ("1", "team/app", "project"),
        ("2", "team/other", "project"),
    ]


@respx.mock
async def test_rate_limited_403_is_retried_as_transient() -> None:
    route = respx.get(f"{API}/projects").mock(
        side_effect=[
            httpx.Response(403, headers={"RateLimit-Remaining": "0"}),
            httpx.Response(200, json=[PROJECT]),
        ]
    )

    connector = _connector()
    catalog = await connector.list_catalog()
    await connector.aclose()

    assert route.call_count == 2
    assert catalog[0].native_id == "1"


class _RecordingThrottle:
    def __init__(self) -> None:
        self.costs: list[int] = []

    async def acquire(self, cost: int = 1) -> None:
        self.costs.append(cost)

    async def feedback(self, status_code: int, headers: httpx.Headers) -> None:
        del status_code, headers


@respx.mock
async def test_manifest_request_cost_reaches_throttle_acquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GitLabConnector, "manifest", replace(GitLabConnector.manifest, request_cost=3)
    )
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[]))
    throttle = _RecordingThrottle()

    connector = GitLabConnector.create(base_url=BASE, credential="secret", throttle=throttle)
    await connector.list_catalog()
    await connector.aclose()

    assert throttle.costs == [3]


@respx.mock
async def test_check_connection_ok() -> None:
    respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"id": 5}))
    respx.get(f"{API}/projects").mock(return_value=httpx.Response(200, json=[]))

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
async def test_check_connection_bad_token_skips_later_steps() -> None:
    respx.get(f"{API}/user").mock(return_value=httpx.Response(401))

    connector = _connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert not diagnosis.ok
    reachability, credentials, permissions = diagnosis.steps
    assert reachability.ok
    assert not credentials.ok
    assert not permissions.ok
    assert permissions.detail == "skipped"
