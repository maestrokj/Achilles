"""Conversation persistence: ownership as 404, feedback, trace, demand (P0)."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.query_engine.constants import MessageRole, Surface
from achilles.query_engine.conversation import store
from achilles.query_engine.models import AccessCounter, Message
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p0]


async def conversation_with_answer(db_session: AsyncSession, user_id: int) -> tuple[int, int]:
    conversation = await store.create(
        db_session, user_id=user_id, surface=Surface.WEB, first_message="question"
    )
    await store.append(
        db_session, conversation_id=conversation.id, role=MessageRole.USER, content="question"
    )
    answer = await store.append(
        db_session,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="answer",
        model="test-model",
        tokens_used=42,
    )
    await db_session.commit()
    return conversation.id, answer.id


async def test_lazy_creation_stamps_owner_surface_and_title(db_session: AsyncSession):
    user = await create_user(db_session)
    conversation = await store.create(
        db_session, user_id=user.id, surface=Surface.WEB, first_message="  how   do we deploy? "
    )
    await db_session.commit()

    assert conversation.title == "how do we deploy?"
    assert conversation.surface == "web"
    assert conversation.selected_model is None


async def test_foreign_conversation_is_404_not_403(db_session: AsyncSession):
    owner = await create_user(db_session)
    stranger = await create_user(db_session)
    conversation_id, _ = await conversation_with_answer(db_session, owner.id)

    with pytest.raises(ApiError) as err:
        await store.get_owned(db_session, conversation_id=conversation_id, user_id=stranger.id)
    assert err.value.status == 404


async def test_feedback_votes_and_clears_on_own_assistant_message(db_session: AsyncSession):
    user = await create_user(db_session)
    _, answer_id = await conversation_with_answer(db_session, user.id)

    await store.set_feedback(db_session, message_id=answer_id, user_id=user.id, value=1)
    assert (await db_session.get_one(Message, answer_id)).feedback == 1

    await store.set_feedback(db_session, message_id=answer_id, user_id=user.id, value=None)
    assert (await db_session.get_one(Message, answer_id)).feedback is None


async def test_feedback_on_foreign_or_user_message_is_404(db_session: AsyncSession):
    user = await create_user(db_session)
    stranger = await create_user(db_session)
    conversation_id, answer_id = await conversation_with_answer(db_session, user.id)

    with pytest.raises(ApiError):
        await store.set_feedback(db_session, message_id=answer_id, user_id=stranger.id, value=1)

    user_message_id = (
        await db_session.execute(
            sa.select(Message.id).where(
                Message.conversation_id == conversation_id, Message.role == "user"
            )
        )
    ).scalar_one()
    with pytest.raises(ApiError):
        await store.set_feedback(db_session, message_id=user_message_id, user_id=user.id, value=1)


async def test_feedback_check_rejects_alien_values(db_session: AsyncSession):
    user = await create_user(db_session)
    _, answer_id = await conversation_with_answer(db_session, user.id)

    message = await db_session.get_one(Message, answer_id)
    message.feedback = 5
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_trace_is_unique_per_message(db_session: AsyncSession):
    user = await create_user(db_session)
    _, answer_id = await conversation_with_answer(db_session, user.id)

    await store.save_trace(
        db_session, message_id=answer_id, search_query="q", candidates=[], citations=[]
    )
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await store.save_trace(
            db_session, message_id=answer_id, search_query="q2", candidates=[], citations=[]
        )
        await db_session.commit()
    await db_session.rollback()


async def test_access_counter_upserts_hits(db_session: AsyncSession):
    await store.bump_access(db_session, [101, 202, 101])  # dupes in one turn count once
    await store.bump_access(db_session, [101])
    await db_session.commit()

    rows = {
        row.entity_ref: row.hits
        for row in (await db_session.execute(sa.select(AccessCounter))).scalars()
    }
    assert rows == {101: 2, 202: 1}


async def test_entity_cited_in_matches_only_cited_entities(db_session: AsyncSession):
    user = await create_user(db_session)
    conversation_id, answer_id = await conversation_with_answer(db_session, user.id)
    await store.save_trace(
        db_session,
        message_id=answer_id,
        search_query="q",
        candidates=[],
        citations=[{"marker": 1, "entity_id": 777, "source_type": "gitlab"}],
    )
    await db_session.commit()

    assert await store.entity_cited_in(db_session, conversation_id=conversation_id, entity_id=777)
    # A candidate that never became a citation, and a wholly unrelated id.
    assert not await store.entity_cited_in(
        db_session, conversation_id=conversation_id, entity_id=778
    )


async def test_entity_cited_in_is_scoped_to_the_conversation(db_session: AsyncSession):
    user = await create_user(db_session)
    _, answer_id = await conversation_with_answer(db_session, user.id)
    other_conversation, _ = await conversation_with_answer(db_session, user.id)
    await store.save_trace(
        db_session,
        message_id=answer_id,
        search_query="q",
        candidates=[],
        citations=[{"marker": 1, "entity_id": 777, "source_type": "gitlab"}],
    )
    await db_session.commit()

    # Same entity, but cited in a different conversation — not a valid click here.
    assert not await store.entity_cited_in(
        db_session, conversation_id=other_conversation, entity_id=777
    )


async def test_cascade_wipes_the_whole_dialogue(db_session: AsyncSession):
    user = await create_user(db_session)
    conversation_id, answer_id = await conversation_with_answer(db_session, user.id)
    await store.save_trace(
        db_session, message_id=answer_id, search_query="q", candidates=[], citations=[]
    )
    await db_session.commit()

    await db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await db_session.commit()

    remaining = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
    ).scalar_one()
    assert remaining == 0
