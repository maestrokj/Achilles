"""Feed API: own-slice visibility, read state, prefs (API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.notifications import dispatcher
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/notifications"


async def _raise_targeted(session: AsyncSession, user_id: int) -> int:
    result = await dispatcher.notify(
        session,
        event="agent.admin_paused",
        target_user_id=user_id,
        params={"agent_name": "Watcher"},
        source_ref="agent/7",
    )
    await session.commit()
    return result.notification_id


async def test_feed_is_own_slice_only(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    other = await create_user(db_session)
    await _raise_targeted(db_session, member.id)
    foreign_id = await _raise_targeted(db_session, other.id)

    await authorize(member.email)
    body = (await client.get(URL)).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["event"] == "agent.admin_paused"
    assert item["title"] == "Агент «Watcher» приостановлен администратором"
    assert item["read_at"] is None

    # someone else's notification is an invisible 404
    assert (await client.post(f"{URL}/{foreign_id}/read")).status_code == 404


async def test_broadcast_lands_in_admin_feeds(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    owner = await create_user(db_session, role="owner")
    member = await create_user(db_session)
    await dispatcher.notify(db_session, event="system.backup_failed")
    await db_session.commit()

    await authorize(owner.email)
    assert (await client.get(f"{URL}/unread")).json() == {"count": 1}

    await authorize(member.email)
    assert (await client.get(f"{URL}/unread")).json() == {"count": 0}


async def test_mark_read_and_read_all_are_idempotent(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    notification_id = await _raise_targeted(db_session, member.id)
    await authorize(member.email)

    assert (await client.post(f"{URL}/{notification_id}/read")).status_code == 204
    assert (await client.post(f"{URL}/{notification_id}/read")).status_code == 204
    assert (await client.get(f"{URL}/unread")).json() == {"count": 0}

    await _raise_targeted(db_session, member.id)
    # the dedup window folds a second admin_paused into the same series —
    # raise a different event for a second unread row
    await dispatcher.notify(
        db_session, event="agent.run_failed", target_user_id=member.id, params={"agent_name": "W"}
    )
    await db_session.commit()
    assert (await client.post(f"{URL}/read-all")).status_code == 204
    assert (await client.get(f"{URL}/unread")).json() == {"count": 0}


async def test_refire_resurfaces_a_read_series_as_unread(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)

    async def raise_series() -> int:
        result = await dispatcher.notify(
            db_session,
            event="agent.run_failed",
            target_user_id=member.id,
            params={"agent_name": "Watcher"},
            dedup_key="agent/7",
        )
        await db_session.commit()
        return result.notification_id

    notification_id = await raise_series()
    await authorize(member.email)

    await client.post(f"{URL}/{notification_id}/read")
    assert (await client.get(f"{URL}/unread")).json() == {"count": 0}

    # the same series fires again inside the dedup window — it folds into the
    # existing row (dedup_count++, last_seen bumped) and must resurface as unread.
    assert await raise_series() == notification_id
    assert (await client.get(f"{URL}/unread")).json() == {"count": 1}
    unread = (await client.get(URL, params={"unread": "true"})).json()
    assert [item["event"] for item in unread["items"]] == ["agent.run_failed"]
    assert unread["items"][0]["dedup_count"] == 2

    # reading the resurfaced series clears it again.
    assert (await client.post(f"{URL}/{notification_id}/read")).status_code == 204
    assert (await client.get(f"{URL}/unread")).json() == {"count": 0}


async def test_unread_facet_filters_the_list(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    read_id = await _raise_targeted(db_session, member.id)
    await dispatcher.notify(
        db_session, event="agent.run_failed", target_user_id=member.id, params={"agent_name": "W"}
    )
    await db_session.commit()
    await authorize(member.email)
    await client.post(f"{URL}/{read_id}/read")

    unread = (await client.get(URL, params={"unread": "true"})).json()
    assert [item["event"] for item in unread["items"]] == ["agent.run_failed"]
    by_type = (await client.get(URL, params={"type": "agent"})).json()
    assert by_type["total"] == 2


async def test_search_matches_title_params_and_source(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    await _raise_targeted(db_session, member.id)  # agent_name="Watcher"
    await dispatcher.notify(
        db_session, event="agent.run_failed", target_user_id=member.id, params={"agent_name": "Owl"}
    )
    await db_session.commit()
    await authorize(member.email)

    # the user-visible name lives in title_params — search reaches it
    hit = (await client.get(URL, params={"q": "Watcher"})).json()
    assert [item["event"] for item in hit["items"]] == ["agent.admin_paused"]

    # a sub-threshold blank of a needle matches nothing
    miss = (await client.get(URL, params={"q": "zzz-nothing"})).json()
    assert miss["total"] == 0

    # the module source slug is searchable and spans both rows
    by_source = (await client.get(URL, params={"q": "agent"})).json()
    assert by_source["total"] == 2


async def test_feed_requires_auth(client: AsyncClient):
    assert (await client.get(URL)).status_code == 401


async def test_prefs_visibility_and_put(
    client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn
):
    member = await create_user(db_session)
    owner = await create_user(db_session, role="owner")

    await authorize(member.email)
    items = (await client.get(f"{URL}/prefs")).json()["items"]
    assert {item["event_type"] for item in items} == {"agent", "account"}
    agent_pref = next(item for item in items if item["event_type"] == "agent")
    assert agent_pref == {"event_type": "agent", "in_app_enabled": True, "email_enabled": False}

    # opt into agent emails
    resp = await client.put(
        f"{URL}/prefs",
        json={"items": [{"event_type": "agent", "in_app_enabled": True, "email_enabled": True}]},
    )
    assert resp.status_code == 200
    updated = next(i for i in resp.json()["items"] if i["event_type"] == "agent")
    assert updated["email_enabled"] is True

    # an org category is out of a member's reach
    denied = await client.put(
        f"{URL}/prefs",
        json={"items": [{"event_type": "sync", "in_app_enabled": False, "email_enabled": False}]},
    )
    assert denied.status_code == 404

    await authorize(owner.email)
    owner_items = (await client.get(f"{URL}/prefs")).json()["items"]
    assert len(owner_items) == 7, "admins see the full matrix of categories"
    sync_pref = next(i for i in owner_items if i["event_type"] == "sync")
    assert sync_pref["email_enabled"] is True, "platform categories default email on"
