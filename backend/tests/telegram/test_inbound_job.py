"""telegram_inbound job: identity resolve, code linking, /new, pointer = conversation."""

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
from achilles.query_engine import service as qe_service
from achilles.query_engine.models import Conversation, Message
from achilles.telegram import jobs
from achilles.telegram.phrases import PHRASES, phrase
from tests.factories.ai import allow_chat, create_model, create_provider
from tests.factories.llm import FakeChatClient, answer_round
from tests.factories.users import create_user
from tests.telegram.conftest import configure_telegram

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", None)
CHAT_ID = "777"
TG_USER = "777"

_SEND = r".*/sendMessage$"


@pytest.fixture(autouse=True)
def job_settings(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(jobs, "app_settings", test_settings)


@pytest.fixture
async def configured(db_session: AsyncSession, test_settings: Settings) -> None:
    await configure_telegram(db_session, test_settings)


@pytest.fixture
def tg_api(hibp_clean: respx.MockRouter) -> respx.MockRouter:
    return hibp_clean


def mock_send_message(router: respx.MockRouter) -> respx.Route:
    return router.post(url__regex=_SEND).mock(
        return_value=Response(200, json={"ok": True, "result": {"message_id": 11}})
    )


def sent_texts(route: respx.Route) -> list[dict[str, object]]:
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


async def run_dm(text: str) -> None:
    await jobs.telegram_inbound(CTX, chat_id=CHAT_ID, tg_user=TG_USER, text=text)


async def link_user(session: AsyncSession, user: User) -> None:
    session.add(IdentityMapping(user_id=user.id, source="telegram", source_user_id=TG_USER))
    await session.commit()


async def test_unlinked_gets_the_hint(
    db_session: AsyncSession, configured: None, tg_api: respx.MockRouter
):
    del db_session
    send = mock_send_message(tg_api)

    await run_dm("what is our roadmap?")

    (body,) = sent_texts(send)
    assert body["chat_id"] == CHAT_ID
    # the seeded org locale, with the deep link to the web-app link page filled
    # in from the job's own configured public base URL
    link_url = messenger_link.link_page_url(jobs.app_settings, "telegram")
    assert body["text"] == phrase("ru", "not_linked", link_url=link_url)
    assert f'href="{link_url}"' in str(body["text"])  # a real clickable anchor, not raw text


async def test_link_code_confirms_and_greets(
    db_session: AsyncSession, configured: None, tg_api: respx.MockRouter
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
    send = mock_send_message(tg_api)

    await run_dm(raw)

    mapping = await db_session.scalar(
        sa.select(IdentityMapping).where(IdentityMapping.source == "telegram")
    )
    assert mapping is not None and mapping.user_id == user.id
    (body,) = sent_texts(send)
    assert user.email in str(body["text"])


async def test_expired_code_gets_the_expired_phrase(
    db_session: AsyncSession, configured: None, tg_api: respx.MockRouter
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
    send = mock_send_message(tg_api)

    await run_dm(raw)

    assert await db_session.scalar(sa.select(sa.func.count(IdentityMapping.id))) == 0
    (body,) = sent_texts(send)
    assert body["text"] == PHRASES["ru"]["link_expired"]


async def test_pointer_continues_one_conversation(
    db_session: AsyncSession,
    configured: None,
    tg_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    mock_send_message(tg_api)
    fake_llm(monkeypatch, "First answer.", "Second answer.")

    await run_dm("first question")
    await run_dm("follow-up")  # no /new — same conversation

    conversations = (await db_session.execute(sa.select(Conversation))).scalars().all()
    assert len(conversations) == 1
    assert conversations[0].surface == "telegram"
    assert conversations[0].meta == {"chat_id": CHAT_ID}
    messages = (await db_session.execute(sa.select(Message).order_by(Message.id))).scalars().all()
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]


async def test_new_command_starts_a_fresh_conversation(
    db_session: AsyncSession,
    configured: None,
    tg_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_send_message(tg_api)
    fake_llm(monkeypatch, "Answer one.", "Answer two.")

    await run_dm("first question")  # conversation A + pointer
    await run_dm("/new")  # clears the pointer, no LLM round
    await run_dm("second question")  # pointer gone → conversation B

    conversations = (await db_session.execute(sa.select(Conversation))).scalars().all()
    assert len(conversations) == 2
    texts = [b["text"] for b in sent_texts(send)]
    assert PHRASES["ru"]["new_conversation"] in texts


async def test_turn_error_posts_an_apology(
    db_session: AsyncSession, configured: None, tg_api: respx.MockRouter
):
    # No chat model allowed → resolve_chat_model refuses with NO_CHAT_MODEL.
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_send_message(tg_api)

    await run_dm("hello?")

    (body,) = sent_texts(send)
    assert "NO_CHAT_MODEL" in str(body["text"])


async def test_deactivated_linked_user_is_told_access_revoked(
    db_session: AsyncSession, configured: None, tg_api: respx.MockRouter
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    user.status = str(UserStatus.DEACTIVATED)
    await db_session.commit()
    send = mock_send_message(tg_api)

    await run_dm("hello?")

    (body,) = sent_texts(send)
    assert body["text"] == PHRASES["ru"]["access_revoked"]


async def test_partial_answer_on_error_is_delivered_not_swallowed(
    db_session: AsyncSession,
    configured: None,
    tg_api: respx.MockRouter,
    chat_model: str,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session)
    await link_user(db_session, user)
    send = mock_send_message(tg_api)

    async def half(_: qe_service.TurnContext) -> qe_service.CollectedTurn:
        return qe_service.CollectedTurn(
            text="Half an answer", citations=[], error_code="PROVIDER_UNAVAILABLE"
        )

    monkeypatch.setattr(qe_service, "collect_turn", half)

    await run_dm("hello?")

    (body,) = sent_texts(send)
    assert "Half an answer" in str(body["text"])
    assert PHRASES["ru"]["turn_cut_off"] in str(body["text"])
