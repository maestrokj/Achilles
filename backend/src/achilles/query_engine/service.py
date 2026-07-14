"""The turn orchestrator: model leads, QE wires (conversation.html#route).

The route resolves ownership and the model allow-list with clean problems
BEFORE the stream starts; everything after the SSE headers — turn setup
included — fails as an `error` frame, never a severed stream. Finalization
lands in one commit: assistant message + trace + access counters + the
usage bucket. A dropped client cancels generation; the partial text is kept
(the history stays coherent, the salvage write is shielded from the
cancellation), tokens_used stays NULL.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import anyio
import sqlalchemy as sa
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import AiFunction
from achilles.ai_foundation.llm.factory import client_for
from achilles.ai_foundation.llm.harness import HarnessTool, ToolRoundStart, run_turn
from achilles.ai_foundation.llm.types import (
    ChatClient,
    ChatMessage,
    ProviderUnavailableError,
    StreamEnd,
    TextDelta,
    Usage,
)
from achilles.ai_foundation.models import AiModel, AiProvider, ChatModel, Tool
from achilles.ai_foundation.services import prompt, tokenizer
from achilles.ai_foundation.services.usage import record_usage
from achilles.ai_foundation.tools.binding import bind_catalog_tool
from achilles.api.problems import CODE_INTERNAL_ERROR, ApiError
from achilles.api.sse import sse_frame
from achilles.knowledge_store.retrieval.hybrid import HiddenHint
from achilles.knowledge_store.services import emptiness
from achilles.query_engine.constants import (
    CODE_MODEL_NOT_ALLOWED,
    CODE_NO_CHAT_MODEL,
    CODE_PROVIDER_UNAVAILABLE,
    HISTORY_BUDGET_TOKENS,
    HISTORY_FETCH_LIMIT,
    RESPONSE_RESERVE_TOKENS,
    FinishReason,
    MessageRole,
)
from achilles.query_engine.conversation import budget, store
from achilles.query_engine.models import Conversation, Message
from achilles.query_engine.rag.citations import resolve as resolve_citations
from achilles.query_engine.rag.search import SEARCH_SPEC, SearchKnowledgeTool, SearchOutcome
from achilles.query_engine.schemas import (
    CitationOut,
    CitationsEvent,
    ConversationEvent,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    GroundingOut,
    MessageEvent,
    ToolRoundEvent,
)

logger = logging.getLogger(__name__)

# Grounding rules travel with the knowledge tools; hub mode omits both
# (grounding.html#modes — with an empty store the mode "does not exist").
GROUNDING_INSTRUCTIONS = (
    "\n\nWhen the question concerns the company, its people, projects or "
    "documents, call search_knowledge with a standalone query before "
    "answering. Ground such answers strictly in the returned fragments and "
    "cite them inline as [n] using the markers provided. If the search "
    "returns nothing relevant, say honestly that you could not find it — "
    "never invent company facts. Purely conversational questions need no "
    "search."
)


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    model: AiModel
    provider: AiProvider


@dataclass(slots=True)
class TurnContext:
    """Everything the stream generator needs, resolved before the first byte."""

    session: AsyncSession
    cache: Redis
    crypto_key: bytes
    user_id: int
    user_locale: str | None  # NULL → org default; the language-policy fallback
    conversation: Conversation
    conversation_created: bool
    user_message: Message
    resolved: ResolvedModel


async def chat_model_rows(
    session: AsyncSession,
) -> list[tuple[AiModel, AiProvider, bool]]:
    """The one query behind both the picker and the resolver — one eligibility rule."""
    rows = await session.execute(
        sa.select(AiModel, AiProvider, ChatModel.is_default)
        .join(ChatModel, ChatModel.model_id == AiModel.id)
        .join(AiProvider, AiProvider.id == AiModel.provider_id)
        # AiModel.is_enabled is the catalogue gate; ChatModel.is_enabled is the
        # admin pausing this entry off the chat surface without removing it.
        .where(AiModel.is_enabled, ChatModel.is_enabled)
        .order_by(ChatModel.id)
    )
    return [(model, provider, is_default) for model, provider, is_default in rows.tuples()]


async def resolve_chat_model(
    session: AsyncSession,
    *,
    requested: str | None,
    conversation_sticky: str | None,
    user_sticky: str | None,
) -> ResolvedModel:
    """The allow-list is the admin's word, honoured in order of intimacy.

    requested (this turn) → the conversation's sticky → the user's personal
    default → the list default. Only the picker surfaces set the two sticky
    layers; where there is no picker both are None and the admin default wins.
    """
    rows = await chat_model_rows(session)
    if not rows:
        raise ApiError(
            409, CODE_NO_CHAT_MODEL, "No chat model", "an Admin must allow chat models first"
        )
    target = requested or conversation_sticky or user_sticky
    if target is not None:
        for model, provider, _ in rows:
            if model.model_id == target:
                return ResolvedModel(model=model, provider=provider)
        if requested is not None:
            raise ApiError(
                422, CODE_MODEL_NOT_ALLOWED, "Model not allowed", f"{target!r} is not in the list"
            )
        # A sticky intent gone stale (model removed from the list) falls
        # through to the default instead of blocking the conversation.
    for model, provider, is_default in rows:
        if is_default:
            return ResolvedModel(model=model, provider=provider)
    raise ApiError(409, CODE_NO_CHAT_MODEL, "No chat model", "the list has no default")


async def _chat_tools(
    context: TurnContext, search_tool: SearchKnowledgeTool | None
) -> list[HarnessTool]:
    tools: list[HarnessTool] = []
    if search_tool is not None:
        tools.append(HarnessTool(spec=SEARCH_SPEC, handler=search_tool))
    rows = (await context.session.execute(sa.select(Tool).where(Tool.chat_enabled))).scalars().all()
    for row in rows:
        bound = bind_catalog_tool(row, crypto_key=context.crypto_key)
        if bound is not None:
            tools.append(bound)
    return tools


async def _system_prompt(context: TurnContext, *, knowledge_tools: bool) -> str:
    text = await prompt.rendered_platform(context.session)
    if knowledge_tools:
        text += GROUNDING_INSTRUCTIONS
    # Last block on purpose: the closing position is what weak models retain
    # when the evidence they read is in another language than the request.
    text += "\n\n" + await prompt.language_policy(
        context.session,
        user_locale=context.user_locale,
        message_text=context.user_message.content,
    )
    return text


def _grounding(
    outcomes: list[SearchOutcome],
    citations: list[CitationOut],
    hidden: HiddenHint | None,
    *,
    offered: bool,
) -> GroundingOut:
    if not offered or not outcomes:
        return GroundingOut(mode="conversational")
    # "empty" = the answer cites nothing. Candidates can't sharpen this call:
    # ANN always returns nearest neighbours, so their presence proves no
    # relevance — the plaque copy therefore claims only the absence of cited
    # sources, which holds whether the model found nothing or didn't cite.
    if citations:
        return GroundingOut(mode="grounded", outcome="found")
    if hidden is not None:
        return GroundingOut(
            mode="grounded",
            outcome="acl_hidden",
            hidden_source_type=hidden.source_type,
            hidden_author_email=hidden.author_email,
        )
    return GroundingOut(mode="grounded", outcome="empty")


async def turn_events(context: TurnContext) -> AsyncGenerator[tuple[str, BaseModel]]:
    """Typed events of one turn; stream_turn wraps them in SSE, collect_turn folds them."""
    session = context.session
    spoken: list[str] = []
    usage: Usage | None = None
    search_tool: SearchKnowledgeTool | None = None
    knowledge_offered = False
    client: ChatClient | None = None
    # The 200/text/event-stream headers hit the wire before this generator
    # runs, so even setup failures must surface as an `error` frame — an
    # exception here would just sever the stream and lose the problem code.
    try:
        counter = await tokenizer.get_token_counter(session) or tokenizer.approx_counter

        turns = [
            (message.role, message.content)
            for message in await store.history(
                session, context.conversation.id, limit=HISTORY_FETCH_LIMIT
            )
            # A failed turn's marker (often empty) is a UI notice, not a real
            # assistant turn — feeding it to the model would poison the context.
            if message.id != context.user_message.id and message.finish != FinishReason.FAILED
        ]
        messages, history_used = budget.trim_history(
            turns, counter=counter, budget_tokens=HISTORY_BUDGET_TOKENS
        )
        messages.append(ChatMessage(role="user", content=context.user_message.content))
        # Whatever history left unspent goes to grounding (one shared pool).
        evidence_budget = budget.evidence_budget(history_used)

        knowledge_offered = not await emptiness.is_empty(session)
        search_tool = (
            SearchKnowledgeTool(
                session,
                context.cache,
                user_id=context.user_id,
                counter=counter,
                evidence_budget=evidence_budget,
            )
            if knowledge_offered
            else None
        )
        tools = await _chat_tools(context, search_tool)
        system = await _system_prompt(context, knowledge_tools=knowledge_offered)

        client = client_for(context.resolved.provider, crypto_key=context.crypto_key)

        if context.conversation_created:
            yield ("conversation", ConversationEvent(id=context.conversation.id))
        yield (
            "message",
            MessageEvent(
                user_message_id=context.user_message.id, model=context.resolved.model.model_id
            ),
        )

        async for event in run_turn(
            client,
            model=context.resolved.model.model_id,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=RESPONSE_RESERVE_TOKENS,
        ):
            match event:
                case TextDelta(text):
                    spoken.append(text)
                    yield ("delta", DeltaEvent(text=text))
                case ToolRoundStart(calls):
                    yield ("tool_round", ToolRoundEvent(tools=[call.name for call in calls]))
                case Usage():
                    usage = event
                case StreamEnd():
                    pass
    except asyncio.CancelledError:
        # The client dropped mid-stream: keep the partial words, tokens stay NULL.
        await _terminal(context, spoken, finish=FinishReason.STOPPED)
        raise
    except ApiError as exc:
        yield ("error", ErrorEvent(code=exc.code, detail=exc.detail))
        await _terminal(context, spoken, finish=FinishReason.FAILED, error_code=exc.code)
        return
    except ProviderUnavailableError as exc:
        # The provider is down, not us — a distinct code lets the UI say so.
        logger.warning("chat provider unavailable: %s", exc)
        yield ("error", ErrorEvent(code=CODE_PROVIDER_UNAVAILABLE, detail=str(exc)))
        await _terminal(
            context, spoken, finish=FinishReason.FAILED, error_code=CODE_PROVIDER_UNAVAILABLE
        )
        return
    except Exception:
        logger.exception("chat turn failed mid-stream")
        yield ("error", ErrorEvent(code=CODE_INTERNAL_ERROR))
        await _terminal(context, spoken, finish=FinishReason.FAILED, error_code=CODE_INTERNAL_ERROR)
        return
    finally:
        if client is not None:
            await client.aclose()

    outcomes = search_tool.outcomes if search_tool else []
    assistant, citations = await _finalize(context, spoken, usage, outcomes)
    # The hidden-ACL probe waits until here — off the streamed answer's path —
    # and fires only when nothing was cited, the one case its plaque is shown.
    hidden = (
        await search_tool.resolve_hidden_hint()
        if search_tool is not None and outcomes and not citations
        else None
    )
    yield ("grounding", _grounding(outcomes, citations, hidden, offered=knowledge_offered))
    if citations:
        yield ("citations", CitationsEvent(items=citations))
    yield ("done", DoneEvent(assistant_message_id=assistant.id, tokens_used=assistant.tokens_used))


async def stream_turn(context: TurnContext) -> AsyncGenerator[str]:
    """SSE frames of one turn; the route has already persisted the user message."""
    events = turn_events(context)
    try:
        async for event, payload in events:
            yield sse_frame(event, payload)
    finally:
        # A consumer that stops at a yield boundary must still unwind the turn.
        await events.aclose()


@dataclass(frozen=True, slots=True)
class CollectedTurn:
    """One turn folded into a single message — the non-SSE surfaces' contract (Slack)."""

    text: str
    citations: list[CitationOut]
    error_code: str | None


async def collect_turn(context: TurnContext) -> CollectedTurn:
    """Consume the whole turn and return the final text with its citations.

    Persistence semantics are identical to the SSE path — turn_events already
    finalized (or salvaged) by the time the generator is exhausted.
    """
    text: list[str] = []
    citations: list[CitationOut] = []
    error_code: str | None = None
    async for _event, payload in turn_events(context):
        match payload:
            case DeltaEvent() as delta:
                text.append(delta.text)
            case CitationsEvent() as frame:
                citations = frame.items
            case ErrorEvent() as frame:
                error_code = frame.code
            case _:
                pass  # conversation/message/tool_round/grounding/done — SSE-only detail
    return CollectedTurn(text="".join(text), citations=citations, error_code=error_code)


async def _terminal(
    context: TurnContext,
    spoken: list[str],
    *,
    finish: FinishReason,
    error_code: str | None = None,
) -> None:
    """Land the turn's terminal marker so a reload stays honest.

    A failed turn always writes its row (even with empty content) — that marker
    is what replays the notice instead of a silent dangling question. A stopped
    turn only persists when there is partial text worth keeping; an instant
    cancel with nothing said leaves just the question, as it should.
    """
    if finish is FinishReason.STOPPED and not spoken:
        return
    # Starlette's disconnect cancellation is level-triggered: without the
    # shield every await below would re-raise CancelledError and the partial
    # text would be lost — the exact contract this function exists to keep.
    with anyio.CancelScope(shield=True):
        await store.append(
            context.session,
            conversation_id=context.conversation.id,
            role=MessageRole.ASSISTANT,
            content="".join(spoken),
            model=context.resolved.model.model_id,
            finish=finish,
            error_code=error_code,
        )
        await context.session.commit()


async def _finalize(
    context: TurnContext,
    spoken: list[str],
    usage: Usage | None,
    outcomes: list[SearchOutcome],
) -> tuple[Message, list[CitationOut]]:
    """One commit: assistant message · trace · demand counters · the spend bucket."""
    session = context.session
    text = "".join(spoken)
    assistant = await store.append(
        session,
        conversation_id=context.conversation.id,
        role=MessageRole.ASSISTANT,
        content=text,
        model=context.resolved.model.model_id,
        tokens_used=usage.input_tokens + usage.output_tokens if usage else None,
    )
    citations: list[CitationOut] = []
    if outcomes:
        packed = [item for outcome in outcomes for item in outcome.packed]
        trace, citations = resolve_citations(text, packed)
        await store.save_trace(
            session,
            message_id=assistant.id,
            search_query=" | ".join(outcome.search_query for outcome in outcomes),
            candidates=[c for outcome in outcomes for c in outcome.candidates],
            citations=trace,
        )
        await store.bump_access(session, [int(str(c["entity_id"])) for c in trace])
    if usage is not None:
        # record_usage commits — landing the whole finalization in one transaction.
        await record_usage(
            session,
            model_pk=context.resolved.model.id,
            function=AiFunction.CHAT,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    else:
        await session.commit()
    return assistant, citations
