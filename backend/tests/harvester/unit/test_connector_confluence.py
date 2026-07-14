"""Confluence connector: fetch/normalize/scope/catalog/diagnosis (unit, respx)."""

from datetime import UTC, datetime

import httpx
import pytest
import respx

from achilles.harvester.connectors.base import AclNative, LinkDraft, PrincipalDraft, RawItem
from achilles.harvester.connectors.confluence import PAGE_SIZE, ConfluenceConnector
from achilles.knowledge_store.constants import AclScope, EntityStatus

pytestmark = [pytest.mark.unit, pytest.mark.p1]

BASE = "https://wiki.acme.test"
SEARCH = f"{BASE}/rest/api/content/search"
SPACES = f"{BASE}/rest/api/space"
CURRENT_USER = f"{BASE}/rest/api/user/current"
CREDENTIAL = "admin@acme.test:api-token"


def make_connector(
    scope_mode: str = "all", scope_list: tuple[str, ...] = ()
) -> ConfluenceConnector:
    return ConfluenceConnector.create(
        base_url=BASE, credential=CREDENTIAL, scope_mode=scope_mode, scope_list=scope_list
    )


def make_page(page_id: str, space: str = "ENG") -> dict[str, object]:
    return {
        "id": page_id,
        "title": f"Page {page_id}",
        "status": "current",
        "space": {"key": space},
    }


def search_page(results: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"results": results})


@respx.mock
async def test_fetch_paginates_and_builds_cql() -> None:
    full_page = [make_page(str(i)) for i in range(PAGE_SIZE)]
    route = respx.get(SEARCH).mock(
        side_effect=[search_page(full_page), search_page([make_page("last")])]
    )
    connector = make_connector()
    items = [item async for item in connector.fetch(None)]
    await connector.aclose()

    assert len(items) == PAGE_SIZE + 1
    assert items[-1].source_entity_id == "last"
    assert all(item.source_type == "page" for item in items)
    assert route.call_count == 2
    first = route.calls[0].request
    assert first.url.params["cql"] == "type IN (page,blogpost) ORDER BY lastmodified ASC"
    assert first.url.params["expand"] == "body.storage,version,history,space,ancestors"
    assert route.calls[1].request.url.params["start"] == str(PAGE_SIZE)


@respx.mock
async def test_fetch_since_adds_lastmodified_clause() -> None:
    route = respx.get(SEARCH).mock(return_value=search_page([]))
    connector = make_connector()
    since = datetime(2026, 6, 1, 8, 30, tzinfo=UTC)
    assert [item async for item in connector.fetch(since)] == []
    await connector.aclose()

    # The threshold is backed off by SINCE_TZ_SKEW (12 h) against instance-timezone drift.
    cql = route.calls[0].request.url.params["cql"]
    assert cql == (
        'type IN (page,blogpost) AND lastmodified >= "2026/05/31 20:30" ORDER BY lastmodified ASC'
    )


@respx.mock
async def test_fetch_scope_filters_spaces_client_side() -> None:
    respx.get(SEARCH).mock(
        return_value=search_page([make_page("1", space="ENG"), make_page("2", space="HR")])
    )
    denying = make_connector(scope_list=("HR",))
    selecting = make_connector(scope_mode="selected", scope_list=("HR",))

    assert [i.source_entity_id async for i in denying.fetch(None)] == ["1"]
    assert [i.source_entity_id async for i in selecting.fetch(None)] == ["2"]
    await denying.aclose()
    await selecting.aclose()


async def test_normalize_maps_all_fields() -> None:
    page = {
        "id": 12345,
        "title": "Runbook",
        "status": "current",
        "space": {"key": "ENG"},
        "body": {"storage": {"value": "<h1>Ops</h1><p>First &amp; second</p><p></p><p>Third</p>"}},
        "version": {"when": "2026-06-02T11:30:00.000Z"},
        "history": {
            "createdDate": "2026-06-01T10:00:00.000+0000",
            "createdBy": {
                "accountId": "acc-1",
                "email": "alice@acme.test",
                "displayName": "Alice",
            },
        },
        "ancestors": [{"id": 1}, {"id": 42}],
        "_links": {"webui": "/spaces/ENG/pages/12345/Runbook"},
    }
    connector = make_connector()
    raw = RawItem(source_type="page", source_entity_id="12345", payload=page)
    entity = connector.normalize(raw)
    await connector.aclose()

    assert entity.title == "Runbook"
    assert entity.body == "Ops\nFirst & second\n\nThird"
    assert entity.status == EntityStatus.FINAL
    assert entity.author == PrincipalDraft(
        source_user_id="acc-1", email="alice@acme.test", display_name="Alice"
    )
    assert entity.url == f"{BASE}/spaces/ENG/pages/12345/Runbook"
    assert entity.source_created_at == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    assert entity.source_updated_at == datetime(2026, 6, 2, 11, 30, tzinfo=UTC)
    assert entity.acl == (AclNative(AclScope.GROUP, "ENG"),)
    assert entity.links == (LinkDraft(relation="child_of", target_kind="page", target_ref="42"),)
    assert entity.meta == {"space": "ENG"}


async def test_normalize_status_mapping_and_defaults() -> None:
    connector = make_connector()
    for source_status, expected in [
        ("draft", EntityStatus.DRAFT),
        ("trashed", EntityStatus.ARCHIVED),
        ("archived", EntityStatus.ARCHIVED),
        ("unknown", None),
    ]:
        entity = connector.normalize(
            RawItem(source_type="page", source_entity_id="1", payload={"status": source_status})
        )
        assert entity.status == expected
        assert entity.body is None
        assert entity.url is None
        assert entity.links == ()
    await connector.aclose()


async def test_fetch_principals_is_empty() -> None:
    connector = make_connector()
    assert [person async for person in connector.fetch_principals()] == []
    await connector.aclose()


@respx.mock
async def test_fetch_groups_collects_read_members() -> None:
    respx.get(SPACES).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"key": "ENG", "name": "Engineering"},
                    {"key": "SECRET", "name": "Secret"},
                ]
            },
        )
    )
    respx.get(f"{SPACES}/ENG/permission").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "operation": {"key": "read", "target": "space"},
                        "subjects": {"user": {"results": [{"accountId": "acc-1"}]}},
                    },
                    {
                        "operation": {"key": "write", "target": "space"},
                        "subjects": {"user": {"results": [{"accountId": "acc-2"}]}},
                    },
                ]
            },
        )
    )
    connector = make_connector(scope_list=("SECRET",))
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    assert len(groups) == 1
    assert groups[0].source_group_id == "ENG"
    assert groups[0].name == "Engineering"
    assert groups[0].kind == "space"
    assert groups[0].member_source_user_ids == ("acc-1",)


@respx.mock
async def test_fetch_groups_membership_falls_back_on_permission_api_error() -> None:
    respx.get(SPACES).mock(
        return_value=httpx.Response(200, json={"results": [{"key": "ENG", "name": "Engineering"}]})
    )
    respx.get(f"{SPACES}/ENG/permission").mock(return_value=httpx.Response(404))
    connector = make_connector()
    groups = [group async for group in connector.fetch_groups()]
    await connector.aclose()

    assert len(groups) == 1
    assert groups[0].member_source_user_ids == ()


@respx.mock
async def test_list_catalog_returns_spaces() -> None:
    respx.get(SPACES).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"key": "ENG", "name": "Engineering"},
                    {"key": "HR", "name": "People"},
                ]
            },
        )
    )
    connector = make_connector()
    catalog = await connector.list_catalog()
    await connector.aclose()

    assert [(obj.native_id, obj.name, obj.kind) for obj in catalog] == [
        ("ENG", "Engineering", "space"),
        ("HR", "People", "space"),
    ]


@respx.mock
async def test_check_connection_all_green() -> None:
    respx.get(CURRENT_USER).mock(return_value=httpx.Response(200, json={"accountId": "acc-1"}))
    respx.get(SPACES).mock(
        return_value=httpx.Response(200, json={"results": [{"key": "ENG", "name": "E"}]})
    )
    connector = make_connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert diagnosis.ok
    assert [step.name for step in diagnosis.steps] == ["reachability", "credentials", "permissions"]


@respx.mock
async def test_check_connection_bad_credentials_skips_permissions() -> None:
    respx.get(CURRENT_USER).mock(return_value=httpx.Response(401))
    spaces = respx.get(SPACES).mock(return_value=httpx.Response(200, json={"results": []}))
    connector = make_connector()
    diagnosis = await connector.check_connection()
    await connector.aclose()

    assert not diagnosis.ok
    steps = {step.name: step for step in diagnosis.steps}
    assert steps["reachability"].ok
    assert not steps["credentials"].ok
    assert not steps["permissions"].ok
    assert steps["permissions"].detail == "skipped"
    assert spaces.call_count == 0
