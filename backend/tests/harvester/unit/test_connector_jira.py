"""Jira connector: fetch/normalize/scope/catalog/diagnosis (unit, respx)."""

import base64
from datetime import UTC, datetime

import httpx
import pytest
import respx

from achilles.harvester.connectors._atlassian import SINCE_TZ_SKEW
from achilles.harvester.connectors.base import (
    AclNative,
    LinkDraft,
    PrincipalDraft,
    RawItem,
)
from achilles.harvester.connectors.jira import PAGE_SIZE, JiraConnector
from achilles.knowledge_store.constants import AclScope, EntityStatus

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://acme.atlassian.test"
SEARCH = f"{BASE}/rest/api/2/search"
PROJECTS = f"{BASE}/rest/api/2/project"
MYSELF = f"{BASE}/rest/api/2/myself"
USERS = f"{BASE}/rest/api/2/users/search"
ASSIGNABLE = f"{BASE}/rest/api/2/user/assignable/search"
CREDENTIAL = "admin@acme.test:api-token"


def make_connector(scope_mode: str = "all", scope_list: tuple[str, ...] = ()) -> JiraConnector:
    return JiraConnector.create(
        base_url=BASE, credential=CREDENTIAL, scope_mode=scope_mode, scope_list=scope_list
    )


def make_issue(key: str, project: str = "ENG") -> dict[str, object]:
    return {
        "key": key,
        "fields": {
            "summary": f"Issue {key}",
            "project": {"key": project},
            "status": {"statusCategory": {"key": "new"}},
        },
    }


def search_page(issues: list[dict[str, object]], total: int) -> httpx.Response:
    return httpx.Response(200, json={"total": total, "issues": issues})


@respx.mock
async def test_fetch_paginates_and_sends_basic_auth() -> None:
    route = respx.get(SEARCH).mock(
        side_effect=[
            search_page([make_issue("ENG-1"), make_issue("ENG-2")], total=3),
            search_page([make_issue("ENG-3")], total=3),
        ]
    )
    connector = make_connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert [item.source_entity_id for item in items] == ["ENG-1", "ENG-2", "ENG-3"]
    assert all(item.source_type == "issue" for item in items)
    assert route.call_count == 2
    first = route.calls[0].request
    assert first.url.params["jql"] == "ORDER BY updated ASC"
    assert first.url.params["maxResults"] == str(PAGE_SIZE)
    assert route.calls[1].request.url.params["startAt"] == "2"
    expected_token = base64.b64encode(CREDENTIAL.encode()).decode()
    assert first.headers["Authorization"] == f"Basic {expected_token}"


@respx.mock
async def test_fetch_since_builds_updated_clause() -> None:
    route = respx.get(SEARCH).mock(return_value=search_page([], total=0))
    connector = make_connector()
    since = datetime(2026, 6, 1, 8, 30, tzinfo=UTC)
    assert [item async for item in connector.fetch(since)] == []
    await connector.aclose()

    # The threshold is backed off by SINCE_TZ_SKEW (12 h) against profile-timezone drift.
    jql = route.calls[0].request.url.params["jql"]
    assert jql == 'updated >= "2026-05-31 20:30" ORDER BY updated ASC'


@respx.mock
async def test_fetch_since_clause_subtracts_tz_skew_margin() -> None:
    route = respx.get(SEARCH).mock(return_value=search_page([], total=0))
    connector = make_connector()
    since = datetime(2026, 6, 1, 8, 30, tzinfo=UTC)
    assert [item async for item in connector.fetch(since)] == []
    await connector.aclose()

    expected_stamp = (since - SINCE_TZ_SKEW).strftime("%Y-%m-%d %H:%M")
    assert f'updated >= "{expected_stamp}"' in route.calls[0].request.url.params["jql"]


@respx.mock
async def test_fetch_selected_scope_lands_in_jql() -> None:
    route = respx.get(SEARCH).mock(
        return_value=search_page([make_issue("ENG-1"), make_issue("HR-1", project="HR")], total=2)
    )
    connector = make_connector(scope_mode="selected", scope_list=("ENG", "OPS"))
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    jql = route.calls[0].request.url.params["jql"]
    assert jql == 'project IN ("ENG", "OPS") ORDER BY updated ASC'
    # Client-side in_scope backs up the server-side clause.
    assert [item.source_entity_id for item in items] == ["ENG-1"]


@respx.mock
async def test_fetch_deny_scope_filters_client_side() -> None:
    route = respx.get(SEARCH).mock(
        return_value=search_page(
            [make_issue("ENG-1"), make_issue("SEC-1", project="SECRET")], total=2
        )
    )
    connector = make_connector(scope_list=("SECRET",))
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert [item.source_entity_id for item in items] == ["ENG-1"]
    assert "project" not in route.calls[0].request.url.params["jql"]


async def test_normalize_maps_all_fields() -> None:
    issue = {
        "key": "ENG-7",
        "fields": {
            "summary": "Fix login",
            "description": "<p>Steps to &amp; reproduce</p>",
            "comment": {
                "comments": [
                    {"author": {"displayName": "Alice"}, "body": "<b>Looks</b> fine"},
                    {"author": {"displayName": "Bob"}, "body": ""},
                ]
            },
            "status": {"statusCategory": {"key": "done"}},
            "reporter": {
                "accountId": "acc-1",
                "emailAddress": "rita@acme.test",
                "displayName": "Rita",
            },
            "created": "2026-06-01T10:00:00.000+0000",
            "updated": "2026-06-02T11:30:00.000+0000",
            "project": {"key": "ENG"},
            "labels": ["auth", "p1"],
            "issuelinks": [
                {"type": {"name": "Blocks"}, "outwardIssue": {"key": "ENG-9"}},
                {"type": {"name": "Blocks"}, "inwardIssue": {"key": "ENG-3"}},
            ],
        },
    }
    connector = make_connector()
    raw = RawItem(source_type="issue", source_entity_id="ENG-7", payload=issue)
    entity = connector.normalize(raw)
    await connector.aclose()

    assert entity.title == "Fix login"
    assert entity.body == "Steps to & reproduce\n\nAlice: Looks fine"
    assert entity.status == EntityStatus.FINAL
    assert entity.author == PrincipalDraft(
        source_user_id="acc-1", email="rita@acme.test", display_name="Rita"
    )
    assert entity.url == f"{BASE}/browse/ENG-7"
    assert entity.source_created_at == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    assert entity.source_updated_at == datetime(2026, 6, 2, 11, 30, tzinfo=UTC)
    assert entity.acl == (AclNative(AclScope.GROUP, "ENG"),)
    assert entity.links == (
        LinkDraft(relation="blocks", target_kind="issue", target_ref="ENG-9"),
        LinkDraft(relation="blocks", target_kind="issue", target_ref="ENG-3"),
    )
    assert entity.meta == {"project": "ENG", "labels": ["auth", "p1"]}


async def test_normalize_minimal_issue_defaults() -> None:
    issue = {
        "key": "ENG-1",
        "fields": {"summary": "Bare", "status": {"statusCategory": {"key": "new"}}},
    }
    connector = make_connector()
    raw = RawItem(source_type="issue", source_entity_id="ENG-1", payload=issue)
    entity = connector.normalize(raw)
    await connector.aclose()

    assert entity.status == EntityStatus.DRAFT
    assert entity.body is None
    assert entity.author is None
    assert entity.acl == ()
    assert entity.links == ()
    assert entity.meta == {}


@respx.mock
async def test_fetch_principals_skips_non_atlassian_accounts() -> None:
    respx.get(USERS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "accountId": "acc-1",
                    "accountType": "atlassian",
                    "emailAddress": "a@acme.test",
                    "displayName": "A",
                },
                {"accountId": "bot-1", "accountType": "app", "displayName": "Bot"},
            ],
        )
    )
    connector = make_connector()
    people = [person async for person in connector.fetch_principals()]
    await connector.aclose()

    assert people == [PrincipalDraft(source_user_id="acc-1", email="a@acme.test", display_name="A")]


@respx.mock
async def test_fetch_groups_respects_scope_and_collects_members() -> None:
    respx.get(PROJECTS).mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "ENG", "name": "Engineering"}, {"key": "SECRET", "name": "Secret"}],
        )
    )
    assignable = respx.get(ASSIGNABLE).mock(
        return_value=httpx.Response(
            200, json=[{"accountId": "acc-1"}, {"accountId": "acc-2"}, {"accountId": "acc-1"}]
        )
    )
    connector = make_connector(scope_list=("SECRET",))
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    assert len(groups) == 1
    assert groups[0].source_group_id == "ENG"
    assert groups[0].name == "Engineering"
    assert groups[0].kind == "project"
    assert groups[0].member_source_user_ids == ("acc-1", "acc-2")
    assert assignable.calls[0].request.url.params["project"] == "ENG"


@respx.mock
async def test_list_catalog_returns_projects() -> None:
    respx.get(PROJECTS).mock(
        return_value=httpx.Response(
            200, json=[{"key": "ENG", "name": "Engineering"}, {"key": "HR", "name": "People"}]
        )
    )
    connector = make_connector()
    catalog = await connector.list_catalog()
    await connector.aclose()

    assert [(obj.native_id, obj.name, obj.kind) for obj in catalog] == [
        ("ENG", "Engineering", "project"),
        ("HR", "People", "project"),
    ]


@respx.mock
async def test_check_connection_all_green() -> None:
    respx.get(MYSELF).mock(return_value=httpx.Response(200, json={"accountId": "acc-1"}))
    respx.get(PROJECTS).mock(return_value=httpx.Response(200, json=[{"key": "ENG", "name": "E"}]))
    connector = make_connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert diagnosis.ok
    assert [step.name for step in diagnosis.steps] == ["reachability", "credentials", "permissions"]


@respx.mock
async def test_check_connection_bad_credentials_skips_permissions() -> None:
    myself = respx.get(MYSELF).mock(return_value=httpx.Response(401))
    projects = respx.get(PROJECTS).mock(return_value=httpx.Response(200, json=[]))
    connector = make_connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert not diagnosis.ok
    steps = {step.name: step for step in diagnosis.steps}
    assert steps["reachability"].ok
    assert not steps["credentials"].ok
    assert not steps["permissions"].ok
    assert steps["permissions"].detail == "skipped"
    assert myself.call_count == 1
    assert projects.call_count == 0
