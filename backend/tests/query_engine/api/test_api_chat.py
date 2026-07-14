"""The first end-to-end slice: chat turn over SSE, grounded and conversational (P0).

LLM and embeddings ride respx; KS data comes from factories. Asserted at all
three layers: the SSE event sequence on the wire, the rows in the DB, and the
exact request bodies the model saw (hub mode = no tools in the schema).
"""

import json
from typing import Any

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.models import ModelUsage
from achilles.knowledge_store.constants import AclScope
from achilles.query_engine.models import AccessCounter, Conversation, Message, RetrievalTrace
from tests.ai_foundation.unit.llm_wire import openai_chunk, openai_sse, openai_text_body
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import (
    BUILTIN_EMBEDDING_MODEL,
    EMBEDDINGS_URL,
    allow_chat,
    assign_embedding,
    basis,
    create_model,
    create_provider,
    mock_embed,
)
from tests.factories.knowledge import acl_scene, create_chunk, create_entity, grant
from tests.factories.users import User, create_user

pytestmark = [pytest.mark.api, pytest.mark.p0]

LLM_BASE = "http://llm.test"
LLM_URL = f"{LLM_BASE}/v1/chat/completions"
CHAT_MODEL = "test-chat"
USAGE = {"prompt_tokens": 100, "completion_tokens": 20}


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        name, data = "", ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        events.append((name, json.loads(data)))
    return events


def event_names(body: str) -> list[str]:
    return [name for name, _ in sse_events(body)]


def payload_of(body: str, event: str) -> dict[str, Any]:
    return next(data for name, data in sse_events(body) if name == event)


def tool_call_body(query: str) -> bytes:
    """Stream #1: the model asks for search_knowledge with a standalone query."""
    arguments = json.dumps({"query": query})
    return openai_sse(
        openai_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_knowledge", "arguments": arguments},
                    }
                ]
            }
        ),
        openai_chunk(finish="tool_calls"),
        openai_chunk(usage=USAGE),
    )


def mock_llm(router: respx.MockRouter, *bodies: bytes) -> respx.Route:
    return router.post(LLM_URL).mock(
        side_effect=[
            Response(200, content=body, headers={"content-type": "text/event-stream"})
            for body in bodies
        ]
    )


@pytest.fixture
async def member(db_session: AsyncSession, authorize: AuthorizeFn) -> User:
    user = await create_user(db_session)
    await authorize(user.email)
    return user


@pytest.fixture
async def chat_model(db_session: AsyncSession) -> str:
    provider = await create_provider(
        db_session, adapter="openai_compatible", kind="local", base_url=LLM_BASE
    )
    model = await create_model(
        db_session, provider_id=provider.id, model_id=CHAT_MODEL, model_type="chat"
    )
    await allow_chat(db_session, model.id, default=True)
    return model.model_id


async def granted_doc(db_session: AsyncSession, user: User, *, text: str) -> int:
    scene = await acl_scene(db_session, user=user)
    entity = await create_entity(db_session, source_id=scene.source.id, source_type="page")
    await create_chunk(
        db_session,
        entity_id=entity.id,
        text=text,
        embedding=basis(0),
        embedding_model=BUILTIN_EMBEDDING_MODEL,
    )
    await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)
    return entity.id


# --- Conversational (hub mode: empty store → no knowledge tools at all) ---


async def test_hub_mode_turn_streams_and_persists(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    route = mock_llm(hibp_clean, openai_text_body("Hi ", "there.", usage=USAGE))

    resp = await client.post("/api/v1/conversations", json={"content": "hello"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    names = event_names(resp.text)
    assert names[0] == "conversation"
    assert names[1] == "message"
    assert "delta" in names
    assert names[-2:] == ["grounding", "done"]
    assert payload_of(resp.text, "grounding") == {
        "mode": "conversational",
        "outcome": None,
        "hidden_source_type": None,
        "hidden_author_email": None,
    }

    # The model saw NO tools: the store is empty, hub mode drops them wholesale.
    request_body = json.loads(route.calls.last.request.read())
    assert "tools" not in request_body

    conversation_id = payload_of(resp.text, "conversation")["id"]
    messages = (
        (
            await db_session.execute(
                sa.select(Message).where(Message.conversation_id == conversation_id)
            )
        )
        .scalars()
        .all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == "Hi there."
    assert messages[1].tokens_used == 120
    assert messages[1].model == CHAT_MODEL

    bucket = (
        await db_session.execute(sa.select(ModelUsage).where(ModelUsage.function == "chat"))
    ).scalar_one()
    assert (bucket.input_tokens, bucket.output_tokens) == (100, 20)


async def test_lazy_title_and_replay(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    mock_llm(hibp_clean, openai_text_body("Answer.", usage=USAGE))
    resp = await client.post("/api/v1/conversations", json={"content": "what is our stack?"})
    conversation_id = payload_of(resp.text, "conversation")["id"]

    replay = await client.get(f"/api/v1/conversations/{conversation_id}")

    assert replay.status_code == 200
    body = replay.json()
    assert body["title"] == "what is our stack?"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]


# --- Grounded (store has content → search_knowledge round) ---


async def test_grounded_turn_cites_traces_and_counts(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    entity_id = await granted_doc(db_session, member, text="Deploy runs through the checklist.")
    await assign_embedding(db_session)
    mock_embed(hibp_clean)
    llm = mock_llm(
        hibp_clean,
        tool_call_body("deploy checklist"),
        openai_text_body("Per the checklist [1].", usage=USAGE),
    )

    resp = await client.post("/api/v1/conversations", json={"content": "how do we deploy?"})

    names = event_names(resp.text)
    assert "tool_round" in names
    assert names[-3:] == ["grounding", "citations", "done"]

    grounding = payload_of(resp.text, "grounding")
    assert grounding["mode"] == "grounded"
    assert grounding["outcome"] == "found"

    citations = payload_of(resp.text, "citations")["items"]
    assert [c["marker"] for c in citations] == [1]
    assert citations[0]["entity_id"] == entity_id
    assert citations[0]["snippet"] == "Deploy runs through the checklist."

    # Stream #1 offered search_knowledge; stream #2 keeps the catalog on the
    # wire (anthropic requires it with tool history) but forbids calling.
    first = json.loads(llm.calls[0].request.read())
    second = json.loads(llm.calls[1].request.read())
    assert [t["function"]["name"] for t in first["tools"]] == ["search_knowledge"]
    assert "tool_choice" not in first
    assert [t["function"]["name"] for t in second["tools"]] == ["search_knowledge"]
    assert second["tool_choice"] == "none"
    assert "search_knowledge" in first["messages"][0]["content"]  # grounding instructions

    trace = (await db_session.execute(sa.select(RetrievalTrace))).scalar_one()
    assert trace.search_query == "deploy checklist"
    assert trace.citations and trace.citations[0]["entity_id"] == entity_id
    assert trace.candidates

    counter = (await db_session.execute(sa.select(AccessCounter))).scalar_one()
    assert (counter.entity_ref, counter.hits) == (entity_id, 1)

    # The replay hydrates citation cards from KS by id — links, not copies.
    conversation_id = payload_of(resp.text, "conversation")["id"]
    replay = (await client.get(f"/api/v1/conversations/{conversation_id}")).json()
    replayed = replay["messages"][-1]["citations"]
    assert replayed[0]["title"] == "Entity" or replayed[0]["title"]  # factory title
    assert replayed[0]["source_type"] == "page"
    assert replayed[0]["snippet"] == "Deploy runs through the checklist."


async def test_not_found_is_an_honest_empty_outcome(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    await granted_doc(db_session, member, text="Completely unrelated content.")
    await assign_embedding(db_session)
    hibp_clean.post(EMBEDDINGS_URL).mock(
        return_value=Response(
            200, json={"data": [{"index": 0, "embedding": basis(5)}], "usage": {}}
        )
    )
    mock_llm(
        hibp_clean,
        tool_call_body("quarterly synergy null"),
        openai_text_body("I could not find that.", usage=USAGE),
    )

    resp = await client.post("/api/v1/conversations", json={"content": "find the unfindable"})

    grounding = payload_of(resp.text, "grounding")
    assert grounding == {
        "mode": "grounded",
        "outcome": "empty",
        "hidden_source_type": None,
        "hidden_author_email": None,
    }
    assert "citations" not in event_names(resp.text)


async def test_acl_hidden_yields_the_content_free_hint(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    # A doc that matches the query but belongs to someone else's group.
    other = await create_user(db_session)
    other_scene = await acl_scene(db_session, user=other)
    hidden = await create_entity(db_session, source_id=other_scene.source.id, source_type="page")
    await create_chunk(db_session, entity_id=hidden.id, text="secret roadmap details")
    await grant(
        db_session,
        entity_id=hidden.id,
        scope=AclScope.GROUP.value,
        source_group_id=other_scene.group.id,
    )
    await assign_embedding(db_session)
    mock_embed(hibp_clean)
    mock_llm(
        hibp_clean,
        tool_call_body("roadmap"),
        openai_text_body("I found nothing you can access.", usage=USAGE),
    )

    resp = await client.post("/api/v1/conversations", json={"content": "show the roadmap"})

    grounding = payload_of(resp.text, "grounding")
    assert grounding["outcome"] == "acl_hidden"
    assert grounding["hidden_source_type"] == "page"


async def test_rag_cache_skips_the_second_embed(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    await granted_doc(db_session, member, text="Deploy checklist lives in the wiki.")
    await assign_embedding(db_session)
    embed = mock_embed(hibp_clean)
    mock_llm(
        hibp_clean,
        tool_call_body("deploy checklist"),
        openai_text_body("See [1].", usage=USAGE),
        tool_call_body("deploy checklist"),
        openai_text_body("Still [1].", usage=USAGE),
    )

    first = await client.post("/api/v1/conversations", json={"content": "deploy?"})
    conversation_id = payload_of(first.text, "conversation")["id"]
    await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "again?"}
    )

    assert embed.call_count == 1  # the exact cache answered the second turn


async def test_empty_search_is_not_cached(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    """A cached empty answer would hide a freshly ingested document for the TTL.

    The store must be non-empty (hub mode offers no search) yet yield zero
    hits: a vector-less chunk keeps ANN silent, the off-topic query keeps
    FTS silent.
    """
    scene = await acl_scene(db_session, user=member)
    entity = await create_entity(db_session, source_id=scene.source.id, source_type="page")
    await create_chunk(db_session, entity_id=entity.id, text="deploy checklist in the wiki")
    await grant(db_session, entity_id=entity.id, scope=AclScope.PUBLIC.value)
    await assign_embedding(db_session)
    embed = mock_embed(hibp_clean)
    mock_llm(
        hibp_clean,
        tool_call_body("quantum blockchain"),
        openai_text_body("Nothing on that.", usage=USAGE),
        tool_call_body("quantum blockchain"),
        openai_text_body("Still nothing.", usage=USAGE),
    )

    first = await client.post("/api/v1/conversations", json={"content": "quantum?"})
    conversation_id = payload_of(first.text, "conversation")["id"]
    await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "sure?"}
    )

    assert embed.call_count == 2  # the empty first answer did not stick in the cache


# --- Model selection ---


async def test_requested_model_sticks_on_the_conversation(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    provider = await create_provider(
        db_session, adapter="openai_compatible", kind="local", base_url=LLM_BASE
    )
    second = await create_model(
        db_session, provider_id=provider.id, model_id="second-chat", model_type="chat"
    )
    await allow_chat(db_session, second.id, default=False)
    mock_llm(hibp_clean, openai_text_body("From the second model.", usage=USAGE))

    resp = await client.post(
        "/api/v1/conversations", json={"content": "hi", "model": "second-chat"}
    )

    assert payload_of(resp.text, "message")["model"] == "second-chat"
    conversation_id = payload_of(resp.text, "conversation")["id"]
    conversation = await db_session.get_one(Conversation, conversation_id)
    assert conversation.selected_model == "second-chat"
    # The pick also becomes the user's personal default for new conversations.
    await db_session.refresh(member)
    assert member.last_chat_model == "second-chat"


async def test_personal_default_seeds_a_fresh_conversation(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    """A new dialogue with no explicit pick runs on the user's last model, not
    the admin default — and the picker reports it via GET /chat/models#selected."""
    provider = await create_provider(
        db_session, adapter="openai_compatible", kind="local", base_url=LLM_BASE
    )
    second = await create_model(
        db_session, provider_id=provider.id, model_id="second-chat", model_type="chat"
    )
    await allow_chat(db_session, second.id, default=False)
    member.last_chat_model = "second-chat"  # a pick carried over from an earlier chat
    await db_session.commit()
    mock_llm(hibp_clean, openai_text_body("From the remembered model.", usage=USAGE))

    resp = await client.post("/api/v1/conversations", json={"content": "hi"})
    assert payload_of(resp.text, "message")["model"] == "second-chat"

    models = (await client.get("/api/v1/chat/models")).json()
    assert models["selected"] == "second-chat"


async def test_stale_personal_default_falls_to_the_admin_default(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    """A personal pick no longer on the allow-list must not block a new turn —
    it silently yields to the admin default, exactly like a stale conversation."""
    member.last_chat_model = "retired-model"
    await db_session.commit()
    mock_llm(hibp_clean, openai_text_body("From the admin default.", usage=USAGE))

    resp = await client.post("/api/v1/conversations", json={"content": "hi"})
    assert payload_of(resp.text, "message")["model"] == CHAT_MODEL


async def test_model_outside_the_allow_list_is_422(
    client: AsyncClient, db_session: AsyncSession, member: User, chat_model: str
):
    resp = await client.post(
        "/api/v1/conversations", json={"content": "hi", "model": "rogue-model"}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "MODEL_NOT_ALLOWED"


async def test_no_chat_models_at_all_is_409(
    client: AsyncClient, db_session: AsyncSession, member: User
):
    resp = await client.post("/api/v1/conversations", json={"content": "hi"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "NO_CHAT_MODEL"


async def test_chat_models_picker_lists_the_allow_list(
    client: AsyncClient, db_session: AsyncSession, member: User, chat_model: str
):
    resp = await client.get("/api/v1/chat/models")
    assert resp.status_code == 200
    body = resp.json()
    assert [(i["model_id"], i["is_default"]) for i in body["items"]] == [(CHAT_MODEL, True)]
    assert body["selected"] is None  # a fresh member has never picked


# --- Ownership & errors ---


async def test_foreign_conversation_is_404_everywhere(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
    authorize: AuthorizeFn,
):
    mock_llm(hibp_clean, openai_text_body("mine", usage=USAGE))
    resp = await client.post("/api/v1/conversations", json={"content": "mine"})
    conversation_id = payload_of(resp.text, "conversation")["id"]

    stranger = await create_user(db_session)
    await authorize(stranger.email)

    assert (await client.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    assert (
        await client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"content": "steal"}
        )
    ).status_code == 404


async def test_anonymous_is_401(client: AsyncClient, db_session: AsyncSession):
    assert (await client.post("/api/v1/conversations", json={"content": "hi"})).status_code == 401
    assert (await client.get("/api/v1/conversations/1")).status_code == 401
    assert (await client.get("/api/v1/chat/models")).status_code == 401


async def test_feedback_roundtrip(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    mock_llm(hibp_clean, openai_text_body("rate me", usage=USAGE))
    resp = await client.post("/api/v1/conversations", json={"content": "hi"})
    assistant_id = payload_of(resp.text, "done")["assistant_message_id"]

    assert (
        await client.patch(f"/api/v1/messages/{assistant_id}/feedback", json={"value": 1})
    ).status_code == 204
    message = await db_session.get_one(Message, assistant_id)
    await db_session.refresh(message)
    assert message.feedback == 1


async def _grounded_click_setup(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    hibp_clean: respx.MockRouter,
) -> tuple[int, int]:
    """One grounded turn — returns (conversation_id, cited entity_id). Citation
    alone leaves AccessCounter.hits == 1 for that entity."""
    entity_id = await granted_doc(db_session, member, text="Deploy runs through the checklist.")
    await assign_embedding(db_session)
    mock_embed(hibp_clean)
    mock_llm(
        hibp_clean,
        tool_call_body("deploy checklist"),
        openai_text_body("Per the checklist [1].", usage=USAGE),
    )
    resp = await client.post("/api/v1/conversations", json={"content": "how do we deploy?"})
    conversation_id = payload_of(resp.text, "conversation")["id"]
    return conversation_id, entity_id


async def test_click_on_cited_source_bumps_demand(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    conversation_id, entity_id = await _grounded_click_setup(client, db_session, member, hibp_clean)

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/access", json={"entity_id": entity_id}
    )
    assert resp.status_code == 204

    counter = (await db_session.execute(sa.select(AccessCounter))).scalar_one()
    assert (counter.entity_ref, counter.hits) == (entity_id, 2)  # citation + click


async def test_click_on_uncited_entity_is_a_silent_no_op(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    conversation_id, entity_id = await _grounded_click_setup(client, db_session, member, hibp_clean)

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/access", json={"entity_id": entity_id + 999}
    )
    assert resp.status_code == 204

    # Only the cited entity exists, still at its citation count — no forged bump.
    rows = {
        row.entity_ref: row.hits
        for row in (await db_session.execute(sa.select(AccessCounter))).scalars()
    }
    assert rows == {entity_id: 1}


async def test_click_on_foreign_conversation_is_404(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
    authorize: AuthorizeFn,
):
    conversation_id, entity_id = await _grounded_click_setup(client, db_session, member, hibp_clean)
    stranger = await create_user(db_session)
    await authorize(stranger.email)

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/access", json={"entity_id": entity_id}
    )
    assert resp.status_code == 404


async def test_click_when_anonymous_is_401(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/api/v1/conversations/1/access", json={"entity_id": 1})
    assert resp.status_code == 401


async def test_provider_error_mid_stream_is_an_error_event(
    client: AsyncClient,
    db_session: AsyncSession,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    hibp_clean.post(LLM_URL).mock(side_effect=httpx.ConnectError("provider down"))

    resp = await client.post("/api/v1/conversations", json={"content": "hi"})

    assert resp.status_code == 200  # the stream had already begun
    names = event_names(resp.text)
    assert names[-1] == "error"
    assert "done" not in names
    # A dead provider is named, not buried under INTERNAL_ERROR.
    assert payload_of(resp.text, "error")["code"] == "PROVIDER_UNAVAILABLE"
    # The user's words survived, AND the broken turn left a terminal marker so a
    # reload can replay the notice — not a silent question with nothing beneath.
    messages = (await db_session.execute(sa.select(Message).order_by(Message.id))).scalars().all()
    assert [m.role for m in messages] == ["user", "assistant"]
    marker = messages[1]
    assert marker.content == ""
    assert marker.finish == "failed"
    assert marker.error_code == "PROVIDER_UNAVAILABLE"
    assert marker.tokens_used is None


async def test_failed_turn_replays_its_notice(
    client: AsyncClient,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    hibp_clean.post(LLM_URL).mock(side_effect=httpx.ConnectError("provider down"))

    resp = await client.post("/api/v1/conversations", json={"content": "hi"})
    conversation_id = payload_of(resp.text, "conversation")["id"]

    replay = await client.get(f"/api/v1/conversations/{conversation_id}")

    body = replay.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    failed = body["messages"][1]
    assert failed["finish"] == "failed"
    assert failed["error_code"] == "PROVIDER_UNAVAILABLE"


async def test_failed_marker_is_kept_out_of_the_model_context(
    client: AsyncClient,
    member: User,
    chat_model: str,
    hibp_clean: respx.MockRouter,
):
    # Turn 1 fails at the provider → an empty failed marker lands in history.
    hibp_clean.post(LLM_URL).mock(side_effect=httpx.ConnectError("down"))
    first = await client.post("/api/v1/conversations", json={"content": "first"})
    conversation_id = payload_of(first.text, "conversation")["id"]

    # Turn 2 succeeds; the model must NOT be shown the empty failed turn.
    hibp_clean.reset()
    route = mock_llm(hibp_clean, openai_text_body("Answer.", usage=USAGE))
    await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "second"}
    )

    sent = json.loads(route.calls.last.request.read())
    dialogue = [m for m in sent["messages"] if m["role"] != "system"]
    # The real question survives; the empty failed assistant marker never reaches
    # the model — no "assistant" role, so its empty content can't poison context.
    assert [m["role"] for m in dialogue] == ["user", "user"]
    assert [m["content"] for m in dialogue] == ["first", "second"]
