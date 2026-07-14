"""Conversation persistence: lazy creation, ownership, append, trace, demand.

A foreign conversation answers 404, not 403 — existence is not disclosed
(protection.html anti-IDOR). All writes flush and leave the commit to the
turn's finalization; the caller owns transaction boundaries.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import CODE_NOT_FOUND, ApiError
from achilles.query_engine.constants import TITLE_MAX_CHARS, FinishReason, MessageRole, Surface
from achilles.query_engine.models import AccessCounter, Conversation, Message, RetrievalTrace


def _not_found() -> ApiError:
    return ApiError(404, CODE_NOT_FOUND, "Not found")


def autogen_title(first_message: str) -> str:
    """First user words as the label; no LLM call for a title."""
    collapsed = " ".join(first_message.split())
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - 1].rstrip() + "…"


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    surface: Surface,
    first_message: str,
    meta: dict[str, object] | None = None,
) -> Conversation:
    conversation = Conversation(
        user_id=user_id, surface=surface, title=autogen_title(first_message), meta=meta
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def get_owned(session: AsyncSession, *, conversation_id: int, user_id: int) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise _not_found()
    return conversation


def sidebar_stmt(user_id: int) -> sa.Select[tuple[Conversation, datetime]]:
    """One user's web conversations with their last activity, freshest first.

    last_activity_at is the created_at of the newest message, falling back to
    the conversation's own created_at; ordering rides that same timestamp, so
    the list stays honest even where ids and timestamps disagree (backdated
    seeds, imports) — the conversation id breaks ties.
    """
    last = (
        sa.select(
            Message.conversation_id.label("conversation_id"),
            sa.func.max(Message.created_at).label("last_message_at"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    last_activity_at = sa.func.coalesce(last.c.last_message_at, Conversation.created_at)
    return (
        sa.select(Conversation, last_activity_at.label("last_activity_at"))
        .outerjoin(last, last.c.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id, Conversation.surface == str(Surface.WEB))
        .order_by(last_activity_at.desc(), Conversation.id.desc())
    )


async def rename(
    session: AsyncSession, *, conversation_id: int, user_id: int, title: str
) -> Conversation:
    conversation = await get_owned(session, conversation_id=conversation_id, user_id=user_id)
    conversation.title = title
    await session.flush()
    return conversation


async def delete(session: AsyncSession, *, conversation_id: int, user_id: int) -> None:
    """Messages and traces follow via FK ondelete=CASCADE."""
    conversation = await get_owned(session, conversation_id=conversation_id, user_id=user_id)
    await session.delete(conversation)
    await session.flush()


async def find_by_meta(
    session: AsyncSession, *, user_id: int, surface: Surface, meta: dict[str, object]
) -> Conversation | None:
    """A surface's thread key lives in meta — JSONB containment (Slack: team/channel/thread_ts)."""
    return await session.scalar(
        sa.select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.surface == str(surface),
            Conversation.meta.op("@>")(sa.cast(meta, JSONB)),
        )
        .order_by(Conversation.id.desc())
        .limit(1)
    )


async def history(
    session: AsyncSession, conversation_id: int, *, limit: int | None = None
) -> list[Message]:
    """Messages oldest-first; `limit` keeps only the newest N (the turn's window)."""
    statement = sa.select(Message).where(Message.conversation_id == conversation_id)
    if limit is None:
        rows = await session.execute(statement.order_by(Message.id))
        return list(rows.scalars())
    rows = await session.execute(statement.order_by(Message.id.desc()).limit(limit))
    return list(rows.scalars())[::-1]


async def append(
    session: AsyncSession,
    *,
    conversation_id: int,
    role: MessageRole,
    content: str,
    model: str | None = None,
    tokens_used: int | None = None,
    finish: FinishReason | None = None,
    error_code: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        model=model,
        tokens_used=tokens_used,
        finish=finish,
        error_code=error_code,
    )
    session.add(message)
    await session.flush()
    return message


async def set_feedback(
    session: AsyncSession, *, message_id: int, user_id: int, value: int | None
) -> None:
    """Vote on an assistant message of one's own conversation; foreign → 404."""
    row = (
        await session.execute(
            sa.select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == message_id,
                Conversation.user_id == user_id,
                Message.role == MessageRole.ASSISTANT,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise _not_found()
    row.feedback = value
    await session.flush()


async def save_trace(
    session: AsyncSession,
    *,
    message_id: int,
    search_query: str,
    candidates: list[dict[str, object]],
    citations: list[dict[str, object]],
) -> None:
    session.add(
        RetrievalTrace(
            message_id=message_id,
            search_query=search_query,
            candidates=candidates,
            citations=citations,
        )
    )
    await session.flush()


async def entity_cited_in(session: AsyncSession, *, conversation_id: int, entity_id: int) -> bool:
    """True if entity_id appears in any answer's citation trace in this conversation.

    Guards the click signal (retrieval.html#access-signal): a click counts only
    for an entity this conversation actually cited, so a forged request body
    cannot inflate the demand of an arbitrary KS entity.
    """
    statement = (
        sa.select(RetrievalTrace.id)
        .join(Message, Message.id == RetrievalTrace.message_id)
        .where(
            Message.conversation_id == conversation_id,
            RetrievalTrace.citations.op("@>")(sa.cast([{"entity_id": entity_id}], JSONB)),
        )
        .limit(1)
    )
    return await session.scalar(statement) is not None


async def bump_access(session: AsyncSession, entity_refs: list[int]) -> None:
    """hits++ per cited KS entity — the demand signal (data-model.html#access-counter).

    Two callers feed it: turn finalization (every citation in an answer) and the
    click on a source card (retrieval.html#access-signal), guarded by
    entity_cited_in.
    """
    if not entity_refs:
        return
    now = datetime.now(UTC)
    statement = pg_insert(AccessCounter).values(
        [{"entity_ref": ref, "hits": 1, "last_accessed_at": now} for ref in set(entity_refs)]
    )
    statement = statement.on_conflict_do_update(
        index_elements=[AccessCounter.entity_ref],
        set_={
            "hits": AccessCounter.hits + 1,
            "last_accessed_at": statement.excluded.last_accessed_at,
        },
    )
    await session.execute(statement)
