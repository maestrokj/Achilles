"""Conversation routes: SSE turns, sidebar list, replay, rename/delete, feedback, model picker.

Everything that can fail cleanly (401/404/409/422) is resolved BEFORE the
stream starts — problems render as problem+json; once SSE began, errors ride
inside the stream as `event: error`. Lazy creation: POST /conversations IS
the first message (data-model.html#conversations).
"""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from achilles.api.pagination import OffsetPage, OffsetParams, offset_window
from achilles.api.sse import SSE_HEADERS
from achilles.auth.dependencies import CurrentUser
from achilles.auth.models import User
from achilles.db.dependencies import DbSession
from achilles.query_engine.constants import MessageRole, Surface
from achilles.query_engine.conversation import store
from achilles.query_engine.models import RetrievalTrace
from achilles.query_engine.rag import citations
from achilles.query_engine.schemas import (
    AccessIn,
    ChatModelOut,
    ChatModelsOut,
    ConversationListItemOut,
    ConversationOut,
    ConversationRenameIn,
    FeedbackIn,
    MessageIn,
    MessageOut,
)
from achilles.query_engine.service import (
    TurnContext,
    chat_model_rows,
    resolve_chat_model,
    stream_turn,
)

router = APIRouter(tags=["conversations"])


async def _start_stream(
    request: Request,
    session: DbSession,
    user: User,
    body: MessageIn,
    conversation_id: int | None,
) -> StreamingResponse:
    if conversation_id is None:
        conversation = await store.create(
            session, user_id=user.id, surface=Surface.WEB, first_message=body.content
        )
        created = True
    else:
        conversation = await store.get_owned(
            session, conversation_id=conversation_id, user_id=user.id
        )
        created = False

    resolved = await resolve_chat_model(
        session,
        requested=body.model,
        conversation_sticky=conversation.selected_model,
        user_sticky=user.last_chat_model,
    )
    if body.model is not None:
        # An explicit pick sticks on both layers: this conversation, and the
        # user's personal default that seeds every new one hereafter.
        conversation.selected_model = body.model
        user.last_chat_model = body.model

    user_message = await store.append(
        session,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=body.content,
    )
    # The words are durable before the model is even dialled.
    await session.commit()

    context = TurnContext(
        session=session,
        cache=request.state.redis.cache,
        crypto_key=request.app.state.crypto_key,
        user_id=user.id,
        user_locale=user.locale,
        conversation=conversation,
        conversation_created=created,
        user_message=user_message,
        resolved=resolved,
    )
    return StreamingResponse(
        stream_turn(context), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/conversations")
async def start_conversation(
    body: MessageIn, user: CurrentUser, session: DbSession, request: Request
) -> StreamingResponse:
    return await _start_stream(request, session, user, body, None)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: MessageIn,
    user: CurrentUser,
    session: DbSession,
    request: Request,
) -> StreamingResponse:
    return await _start_stream(request, session, user, body, conversation_id)


@router.get("/conversations")
async def list_conversations(
    user: CurrentUser, session: DbSession, params: Annotated[OffsetParams, Depends()]
) -> OffsetPage[ConversationListItemOut]:
    """The sidebar: one's own web dialogues, freshest activity first."""
    stmt = store.sidebar_stmt(user.id)
    total, page = await offset_window(session, stmt, params)
    rows = await session.execute(stmt.offset((page - 1) * params.per_page).limit(params.per_page))
    return OffsetPage(
        items=[
            ConversationListItemOut(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                last_activity_at=last_activity_at,
            )
            for conversation, last_activity_at in rows
        ],
        total=total,
        page=page,
        per_page=params.per_page,
    )


@router.patch("/conversations/{conversation_id}", status_code=204)
async def rename_conversation(
    conversation_id: int, body: ConversationRenameIn, user: CurrentUser, session: DbSession
) -> None:
    await store.rename(session, conversation_id=conversation_id, user_id=user.id, title=body.title)
    await session.commit()


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, user: CurrentUser, session: DbSession) -> None:
    await store.delete(session, conversation_id=conversation_id, user_id=user.id)
    await session.commit()


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int, user: CurrentUser, session: DbSession
) -> ConversationOut:
    conversation = await store.get_owned(session, conversation_id=conversation_id, user_id=user.id)
    messages = await store.history(session, conversation_id)
    traces = (
        (
            await session.execute(
                sa.select(RetrievalTrace.message_id, RetrievalTrace.citations).where(
                    RetrievalTrace.message_id.in_([m.id for m in messages])
                )
            )
        )
        .tuples()
        .all()
    )
    cards = await citations.hydrate(session, traces)
    out = [
        MessageOut(
            id=message.id,
            role=message.role,
            content=message.content,
            model=message.model,
            tokens_used=message.tokens_used,
            feedback=message.feedback,
            created_at=message.created_at,
            citations=cards.get(message.id),
            finish=message.finish,
            error_code=message.error_code,
        )
        for message in messages
    ]
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        selected_model=conversation.selected_model,
        created_at=conversation.created_at,
        messages=out,
    )


@router.patch("/messages/{message_id}/feedback", status_code=204)
async def set_feedback(
    message_id: int, body: FeedbackIn, user: CurrentUser, session: DbSession
) -> None:
    await store.set_feedback(session, message_id=message_id, user_id=user.id, value=body.value)
    await session.commit()


@router.post("/conversations/{conversation_id}/access", status_code=204)
async def record_access(
    conversation_id: int, body: AccessIn, user: CurrentUser, session: DbSession
) -> None:
    """A click on a source card bumps that entity's demand (retrieval.html#access-signal).

    A foreign conversation is 404. A click for an entity this conversation never
    cited is a silent no-op — the guard against inflating arbitrary entities.
    """
    await store.get_owned(session, conversation_id=conversation_id, user_id=user.id)
    if not await store.entity_cited_in(
        session, conversation_id=conversation_id, entity_id=body.entity_id
    ):
        return
    await store.bump_access(session, [body.entity_id])
    await session.commit()


@router.get("/chat/models")
async def chat_models(user: CurrentUser, session: DbSession) -> ChatModelsOut:
    """The picker's allow-list — any authenticated account (conversation.html#route)."""
    rows = await chat_model_rows(session)
    return ChatModelsOut(
        items=[
            ChatModelOut(
                model_id=model.model_id, display_name=model.display_name, is_default=is_default
            )
            for model, _, is_default in rows
        ],
        selected=user.last_chat_model,
    )
