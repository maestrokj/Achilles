"""mattermost_inbound job: identity resolve, code linking, thread = conversation."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import respx
import sqlalchemy as sa
from httpx import Response
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.auth.constants import UserStatus
from achilles.auth.models import IdentityMapping, LinkToken, User
from achilles.auth.security.tokens import generate_link_code, hash_token, normalize_link_code
from achilles.auth.services import messenger_link
from achilles.config import Settings
from achilles.mattermost import jobs
from achilles.mattermost.phrases import PHRASES
from achilles.query_engine import service as qe_service
from achilles.query_engine.models import Conversation, Message
from tests.factories.ai import allow_chat, create_model, create_provider
from tests.factories.llm import FakeChatClient, answer_round
from tests.factories.users import create_user
from tests.mattermost.conftest import configure_mattermost

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", None)
CHANNEL_ID = "dm-channel-1"
MM_USER = "person-1"

_POSTS = r".*/api/v4/posts$"


@pytest.fixture(autouse=True)
def job_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


@pytest.fixture
async def configured(db_session: AsyncSession, test_settings: Settings) -> None:
    await configure_mattermost(db_session, test_settings)


@pytest.fixture
def mm_api(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    return hibp_clean


def mock_create_post(router: respx.MockRouter) -> respx.Route:
    return router.post(url__regex=_POSTS).mock(return_value=Response(201, json={"id": "reply-1"}))


def sent_posts(route: respx.Route) -> list[dict[str, object]]:
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

    def _client_for(*_args: object, **_kwargs: object) -> FakeChatClient:
        return client

    monkeypatch.setattr(qe_service, "client_for", _client_for)
    return client


async def run_dm(text: str, *, post_id: str = "post-1", root_id: str | None = None) -> None:
    await jobs.mattermost_inbound(
        CTX, channel_id=CHANNEL_ID, mm_user=MM_USER, text=text, post_id=post_id, root_id=root_id
    )


async def link_user(session: AsyncSession, user: User) -> None:
    session.add(IdentityMapping(user_id=user.id, source="mattermost", source_user_id=MM_USER))
    await session.commit()


async def test_unlinked_gets_the_hint_in_the_thread(
    db_session: AsyncSession, configured: None, mm_api: respx.MockRouter
):
    del db_session
    send = mock_create_post(mm_api)

    await run_dm("what is our roadmap?")

    (body,) = sent_posts(send)
    assert body["channel_id"] == CHANNEL_ID
    assert body["root_id"] == "post-1"  # the root DM starts the thread at itself
    # the seeded org locale, with the deep link rendered as a Markdown anchor
    link_url = messenger_link.link_page_url(jobs.app_settings, "mattermost")
    assert f"]({link_url})" in str(body["message"])


async def test_link_code_confirms_and_greets(
    db_session: AsyncSession, configured: None, mm_api: respx.MockRouter
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
    send = mock_create_post(mm_api)

    await run_dm(raw)

    mapping = await db_session.scalar(
        sa.select(IdentityMapping).where(IdentityMapping.source == "mattermost")
    )
    assert mapping is not None and mapping.user_id == user.id
    (body,) = sent_posts(send)
    assert user.email in str(body["message"])


async def test_expired_code_gets_the_expired_phrase(
    db_session: AsyncSession, configured: None, mm_api: respx.MockRouter
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
    send = mock_create_post(mm_api)

    await run_dm(raw)

    assert await db_session.scalar(sa.select(sa.func.count(IdentityMapping.id))) == 0
    (body,) = sent_posts(send)
    assert body["message"] == PHRASES["ru"]["link_expired"]


async def test_thread_reply_continues_the_conversation(
    db_session: AsyncSession,
    configured: None,
    mm_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_create_post(mm_api)
    fake_llm(monkeypatch, "First answer.", "Second answer.")

    await run_dm("first question", post_id="root-1")
    # a reply inside the thread carries the root's id — same conversation
    await run_dm("follow-up", post_id="post-2", root_id="root-1")

    conversations = (await db_session.execute(sa.select(Conversation))).scalars().all()
    assert len(conversations) == 1
    assert conversations[0].surface == "mattermost"
    assert conversations[0].meta == {"channel_id": CHANNEL_ID, "root_id": "root-1"}
    messages = (await db_session.execute(sa.select(Message).order_by(Message.id))).scalars().all()
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    # every reply is threaded under the same root
    assert all(body["root_id"] == "root-1" for body in sent_posts(send))


async def test_new_root_message_starts_a_fresh_conversation(
    db_session: AsyncSession,
    configured: None,
    mm_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    mock_create_post(mm_api)
    fake_llm(monkeypatch, "Answer one.", "Answer two.")

    await run_dm("first question", post_id="root-1")
    await run_dm("second question", post_id="root-2")  # a new root = a new thread

    conversations = (await db_session.execute(sa.select(Conversation))).scalars().all()
    assert len(conversations) == 2


async def test_turn_error_posts_an_apology(
    db_session: AsyncSession, configured: None, mm_api: respx.MockRouter
):
    # No chat model allowed → resolve_chat_model refuses with NO_CHAT_MODEL.
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_create_post(mm_api)

    await run_dm("hello?")

    (body,) = sent_posts(send)
    assert "NO_CHAT_MODEL" in str(body["message"])


async def test_deactivated_linked_user_is_told_access_revoked(
    db_session: AsyncSession, configured: None, mm_api: respx.MockRouter
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    user.status = str(UserStatus.DEACTIVATED)
    await db_session.commit()
    send = mock_create_post(mm_api)

    await run_dm("hello?")

    (body,) = sent_posts(send)
    assert body["message"] == PHRASES["ru"]["access_revoked"]


async def test_partial_answer_on_error_is_delivered_not_swallowed(
    db_session: AsyncSession,
    configured: None,
    mm_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_create_post(mm_api)

    async def half(_: qe_service.TurnContext) -> qe_service.CollectedTurn:
        return qe_service.CollectedTurn(
            text="Half an answer", citations=[], error_code="PROVIDER_UNAVAILABLE"
        )

    monkeypatch.setattr(qe_service, "collect_turn", half)

    await run_dm("hello?")

    (body,) = sent_posts(send)
    assert "Half an answer" in str(body["message"])
    assert PHRASES["ru"]["turn_cut_off"] in str(body["message"])


async def test_switched_off_between_event_and_pickup_stays_silent(
    db_session: AsyncSession, test_settings: Settings, mm_api: respx.MockRouter
):
    await configure_mattermost(db_session, test_settings, enabled=False)
    send = mock_create_post(mm_api)

    await run_dm("hello?")

    assert not send.called
