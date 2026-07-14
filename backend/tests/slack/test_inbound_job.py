"""slack_inbound job: identity resolve, linking, thread = conversation (integration)."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.constants import CODE_ALREADY_LINKED, UserStatus
from achilles.auth.models import IdentityMapping, LinkToken, User
from achilles.auth.security.tokens import generate_link_code, hash_token, normalize_link_code
from achilles.auth.services import messenger_link
from achilles.config import Settings
from achilles.query_engine import service as qe_service
from achilles.query_engine.models import Conversation, Message
from achilles.slack import jobs
from achilles.slack.constants import SLACK_API_BASE_URL
from achilles.slack.phrases import PHRASES, phrase
from tests.factories.ai import allow_chat, create_model, create_provider
from tests.factories.llm import FakeChatClient, answer_round
from tests.factories.users import create_user
from tests.slack.conftest import TEAM, configure_slack

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", None)
CHANNEL = "D42"
SLACK_USER = "U777"
ROOT_TS = "1720000000.000100"


@pytest.fixture(autouse=True)
def job_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


@pytest.fixture
async def configured(db_session: AsyncSession, test_settings: Settings) -> None:
    await configure_slack(db_session, test_settings)


@pytest.fixture
def slack_api(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    return hibp_clean


def mock_post_message(router: respx.MockRouter) -> respx.Route:
    return router.post(f"{SLACK_API_BASE_URL}/chat.postMessage").mock(
        return_value=Response(200, json={"ok": True, "ts": "1720000000.000200"})
    )


def posted_texts(route: respx.Route) -> list[dict[str, object]]:
    return [json.loads(call.request.content) for call in route.calls]


@pytest.fixture
async def chat_model(db_session: AsyncSession) -> str:
    provider = await create_provider(
        db_session, adapter="openai_compatible", kind="local", base_url="http://llm.test"
    )
    model = await create_model(
        db_session, provider_id=provider.id, model_id="fake-chat", model_type="chat"
    )
    await allow_chat(db_session, model.id, default=True)
    return model.model_id


def fake_llm(monkeypatch: pytest.MonkeyPatch, *texts: str) -> FakeChatClient:
    client = FakeChatClient(rounds=[answer_round(text) for text in texts])
    monkeypatch.setattr(qe_service, "client_for", lambda *a, **kw: client)
    return client


async def run_dm(text: str, *, thread_ts: str | None = None, ts: str = ROOT_TS) -> None:
    await jobs.slack_inbound(
        CTX,
        team=TEAM,
        channel=CHANNEL,
        slack_user=SLACK_USER,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
    )


async def link_user(session: AsyncSession, user: User) -> None:
    session.add(IdentityMapping(user_id=user.id, source="slack", source_user_id=SLACK_USER))
    await session.commit()


async def test_unlinked_without_email_gets_the_hint(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    del db_session
    slack_api.get(url__startswith=f"{SLACK_API_BASE_URL}/users.info").mock(
        return_value=Response(200, json={"ok": False, "error": "missing_scope"})
    )
    post = mock_post_message(slack_api)

    await run_dm("what is our roadmap?")

    (body,) = posted_texts(post)
    assert body["thread_ts"] == ROOT_TS
    # the seeded org locale, with a deep link to the web-app link page built from
    # the job's own configured public base URL (mrkdwn, not raw text)
    link_url = messenger_link.link_page_url(jobs.app_settings, "slack")
    assert body["text"] == phrase("ru", "not_linked", link_url=link_url)
    assert f"<{link_url}|" in str(body["text"])


async def test_auto_link_by_provisioned_email_then_answers(
    db_session: AsyncSession,
    configured: None,
    slack_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    slack_api.get(url__startswith=f"{SLACK_API_BASE_URL}/users.info").mock(
        return_value=Response(200, json={"ok": True, "user": {"profile": {"email": user.email}}})
    )
    post = mock_post_message(slack_api)
    fake_llm(monkeypatch, "Here is the plan.")

    await run_dm("what is our roadmap?")

    mapping = await db_session.scalar(
        sa.select(IdentityMapping).where(IdentityMapping.source == "slack")
    )
    assert mapping is not None and mapping.user_id == user.id
    (body,) = posted_texts(post)
    assert body["text"] == "Here is the plan."


async def test_auto_link_off_falls_back_to_the_hint(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    # With auto-link disabled, even a valid provisioned email must not claim an
    # account — the bot asks for a code (not_linked) instead of linking silently.
    user = await create_user(db_session)
    await db_session.execute(
        sa.text("UPDATE slack_settings SET auto_link_by_email = false WHERE id = 1")
    )
    await db_session.commit()
    info = slack_api.get(url__startswith=f"{SLACK_API_BASE_URL}/users.info").mock(
        return_value=Response(200, json={"ok": True, "user": {"profile": {"email": user.email}}})
    )
    post = mock_post_message(slack_api)

    await run_dm("what is our roadmap?")

    assert await db_session.scalar(sa.select(sa.func.count(IdentityMapping.id))) == 0
    assert not info.called, "auto-link off skips the users.info round-trip entirely"
    (body,) = posted_texts(post)
    link_url = messenger_link.link_page_url(jobs.app_settings, "slack")
    assert body["text"] == phrase("ru", "not_linked", link_url=link_url)


async def test_link_code_confirms_and_greets(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    user = await create_user(db_session)
    raw = generate_link_code()
    db_session.add(
        LinkToken(
            user_id=user.id,
            code_hash=hash_token(normalize_link_code(raw)),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    )
    await db_session.commit()
    # A code-shaped DM goes straight to the link path — no users.info round-trip.
    post = mock_post_message(slack_api)

    await run_dm(raw)

    mapping = await db_session.scalar(
        sa.select(IdentityMapping).where(IdentityMapping.source == "slack")
    )
    assert mapping is not None and mapping.user_id == user.id
    (body,) = posted_texts(post)
    assert user.email in str(body["text"])


async def test_expired_code_gets_the_expired_phrase(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    user = await create_user(db_session)
    raw = generate_link_code()
    db_session.add(
        LinkToken(
            user_id=user.id,
            code_hash=hash_token(normalize_link_code(raw)),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await db_session.commit()
    post = mock_post_message(slack_api)

    await run_dm(raw)

    assert await db_session.scalar(sa.select(sa.func.count(IdentityMapping.id))) == 0
    (body,) = posted_texts(post)
    assert body["text"] == PHRASES["ru"]["link_expired"]


async def test_thread_continues_one_conversation(
    db_session: AsyncSession,
    configured: None,
    slack_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    post = mock_post_message(slack_api)
    fake_llm(monkeypatch, "First answer.", "Second answer.")

    # Root DM starts a conversation whose thread key is the root ts…
    await run_dm("first question")
    # …and the reply in that thread continues it.
    await run_dm("follow-up", ts="1720000000.000300", thread_ts=ROOT_TS)

    conversations = (await db_session.execute(sa.select(Conversation))).scalars().all()
    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.surface == "slack"
    assert conversation.meta == {"team": TEAM, "channel": CHANNEL, "thread_ts": ROOT_TS}
    messages = (await db_session.execute(sa.select(Message).order_by(Message.id))).scalars().all()
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    bodies = posted_texts(post)
    assert [b["thread_ts"] for b in bodies] == [ROOT_TS, ROOT_TS]


async def test_turn_error_posts_an_apology(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    # No chat model allowed → resolve_chat_model refuses with NO_CHAT_MODEL.
    user = await create_user(db_session)
    await link_user(db_session, user)
    post = mock_post_message(slack_api)

    await run_dm("hello?")

    (body,) = posted_texts(post)
    assert "NO_CHAT_MODEL" in str(body["text"])


async def test_deactivated_linked_user_is_told_access_revoked(
    db_session: AsyncSession, configured: None, slack_api: respx.MockRouter
):
    # Linked, then the admin deactivates the account: the link-code prompt would
    # be a dead end, so the bot names the revocation instead.
    user = await create_user(db_session)
    await link_user(db_session, user)
    user.status = str(UserStatus.DEACTIVATED)
    await db_session.commit()
    post = mock_post_message(slack_api)

    await run_dm("hello?")

    (body,) = posted_texts(post)
    assert body["text"] == PHRASES["ru"]["access_revoked"]


async def test_auto_link_race_adopts_the_winners_mapping(
    db_session: AsyncSession,
    configured: None,
    slack_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    # Two first DMs race: the other job links first, our flush would 409. The
    # job must adopt the existing mapping and still answer, not crash.
    user = await create_user(db_session)
    slack_api.get(url__startswith=f"{SLACK_API_BASE_URL}/users.info").mock(
        return_value=Response(200, json={"ok": True, "user": {"profile": {"email": user.email}}})
    )
    post = mock_post_message(slack_api)
    fake_llm(monkeypatch, "Answer after the race.")

    async def racing(session: AsyncSession, **_: object) -> User:
        # Stand in for the concurrent winner: commit the mapping, then conflict.
        session.add(IdentityMapping(user_id=user.id, source="slack", source_user_id=SLACK_USER))
        await session.commit()
        raise ApiError(409, CODE_ALREADY_LINKED, "Conflict", "already linked")

    monkeypatch.setattr(jobs.messenger_link, "auto_link_by_email", racing)

    await run_dm("what is our roadmap?")

    (body,) = posted_texts(post)
    assert body["text"] == "Answer after the race."


async def test_partial_answer_on_error_is_delivered_not_swallowed(
    db_session: AsyncSession,
    configured: None,
    slack_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    # A turn that errors mid-stream persists its partial text (and the web
    # surface showed it); Slack must deliver the same text, not only an apology,
    # or the thread history diverges from what the user saw.
    user = await create_user(db_session)
    await link_user(db_session, user)
    post = mock_post_message(slack_api)

    async def half(_: qe_service.TurnContext) -> qe_service.CollectedTurn:
        return qe_service.CollectedTurn(
            text="Half an answer", citations=[], error_code="PROVIDER_UNAVAILABLE"
        )

    monkeypatch.setattr(qe_service, "collect_turn", half)

    await run_dm("hello?")

    (body,) = posted_texts(post)
    assert "Half an answer" in str(body["text"])
    assert PHRASES["ru"]["turn_cut_off"] in str(body["text"])
