"""The shared road one inbound DM walks on every messenger surface.

identity → (deactivated | adapter command | link code | auto-link | hint) →
dialogue turn → post-back. Extracted verbatim from the Slack/Telegram twins;
each surface plugs in only what its platform dictates: how to send (`Say`),
where a conversation begins and ends (`resolve_conversation`), its markup
(`SurfaceProfile.format_text`) and any platform-only moves (auto-link,
`/new`-style commands) as optional hooks.

The `say` closure owns its client's exception types and swallows transport
errors — by the time we post back, the turn is already persisted, so a failed
delivery must not fail (and retry) the job.
"""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.constants import CODE_ALREADY_LINKED, CODE_LINK_EXPIRED
from achilles.auth.models import User
from achilles.auth.security.tokens import looks_like_link_code
from achilles.auth.services import messenger_link
from achilles.config import Settings
from achilles.db.connections import close_connections, create_connections
from achilles.infra.redis import RedisPools, close_redis_pools, create_redis_pools
from achilles.knowledge_store.services import platform as platform_service
from achilles.messenger.phrases import PhraseFn
from achilles.query_engine import service as qe_service
from achilles.query_engine.constants import MessageRole
from achilles.query_engine.conversation import store
from achilles.query_engine.models import Conversation

logger = logging.getLogger(__name__)

type Say = Callable[[str], Awaitable[None]]
type AutoLink = Callable[[], Awaitable[User | None]]
type Command = Callable[[str], Awaitable[str | None]]  # reply text if it was a command
type OnConversationPersisted = Callable[[Conversation], Awaitable[None]]


class ResolveConversation(Protocol):
    """Where the surface draws its session boundary (thread, pointer, …)."""

    def __call__(self, *, user: User, text: str) -> Awaitable[tuple[Conversation, bool]]: ...


@dataclass(frozen=True, slots=True)
class SurfaceProfile:
    """The per-platform constants of the pipeline."""

    platform: str  # identity_mapping source and /link/{platform} slug
    phrase: PhraseFn
    format_text: Callable[[str], str]
    sources_block: Callable[..., str]  # (citations, *, heading) -> block


@dataclass(frozen=True, slots=True)
class JobContext:
    """Everything a messenger job needs, opened once per job run."""

    session: AsyncSession
    redis: RedisPools
    crypto_key: bytes
    locale: str
    settings: Settings


@asynccontextmanager
async def job_connections(settings: Settings) -> AsyncGenerator[JobContext]:
    """The worker-job connection scaffold: own DB/Redis from the caller's settings.

    Settings arrive as a parameter (never imported here) so each platform's jobs
    module keeps its patchable `app_settings` global.
    """
    crypto_key = settings.derived_crypto_key()
    db = create_connections(settings)
    redis = create_redis_pools(settings)
    try:
        async with db.pg_session_factory() as session:
            locale = (await platform_service.get_platform_settings(session)).locale
            yield JobContext(
                session=session,
                redis=redis,
                crypto_key=crypto_key,
                locale=locale,
                settings=settings,
            )
    finally:
        await close_redis_pools(redis)
        await close_connections(db)


async def handle_inbound(
    ctx: JobContext,
    say: Say,
    *,
    profile: SurfaceProfile,
    platform_user_id: str,
    text: str,
    resolve_conversation: ResolveConversation,
    on_conversation_persisted: OnConversationPersisted | None = None,
    auto_link: AutoLink | None = None,
    command: Command | None = None,
) -> None:
    """One inbound DM: resolve who wrote, then link or answer."""
    linked, user = await messenger_link.resolve_identity(
        ctx.session, platform=profile.platform, platform_user_id=platform_user_id
    )
    if linked:
        if user is None:
            # The mapping stands but the account was deactivated — a code cannot
            # revive it (confirm_code would 409), so name the dead end plainly.
            await say(profile.phrase(ctx.locale, "access_revoked"))
            return
        if command is not None:
            reply = await command(text)
            if reply is not None:
                await say(reply)
                return
        await answer(
            ctx,
            say,
            profile=profile,
            user=user,
            text=text,
            resolve_conversation=resolve_conversation,
            on_conversation_persisted=on_conversation_persisted,
        )
        return

    # Unlinked. A code-shaped DM is a link attempt; check that before any
    # auto-link round-trip so ordinary questions never touch the link guard
    # and a code never leaks into the model as a chat message.
    stripped = text.strip()
    if looks_like_link_code(stripped):
        await confirm_link(
            ctx, say, profile=profile, raw_code=stripped, platform_user_id=platform_user_id
        )
        return

    user = await auto_link() if auto_link is not None else None
    if user is None:
        # Membership grants nothing — hand back a deep link to the code screen.
        link_url = messenger_link.link_page_url(ctx.settings, profile.platform)
        await say(profile.phrase(ctx.locale, "not_linked", link_url=link_url))
        return
    await answer(
        ctx,
        say,
        profile=profile,
        user=user,
        text=text,
        resolve_conversation=resolve_conversation,
        on_conversation_persisted=on_conversation_persisted,
    )


async def confirm_link(
    ctx: JobContext,
    say: Say,
    *,
    profile: SurfaceProfile,
    raw_code: str,
    platform_user_id: str,
) -> None:
    """The DM'ed one-time code, via the same service the HTTP confirm uses."""
    now = datetime.now(UTC)
    try:
        await messenger_link.guard_chat_attempts(
            ctx.redis.durable, platform=profile.platform, chat_id=platform_user_id, now=now
        )
        user = await messenger_link.confirm_code(
            ctx.session,
            raw_code=raw_code,
            platform=profile.platform,
            platform_user_id=platform_user_id,
            platform_email=None,
            now=now,
        )
        await ctx.session.commit()
    except ApiError as exc:
        await ctx.session.rollback()
        key = {
            CODE_LINK_EXPIRED: "link_expired",
            CODE_ALREADY_LINKED: "already_linked",
        }.get(exc.code, "too_many_attempts")
        await say(profile.phrase(ctx.locale, key))
        return
    await say(profile.phrase(ctx.locale, "linked", email=user.email))


async def answer(
    ctx: JobContext,
    say: Say,
    *,
    profile: SurfaceProfile,
    user: User,
    text: str,
    resolve_conversation: ResolveConversation,
    on_conversation_persisted: OnConversationPersisted | None = None,
) -> None:
    """One dialogue turn through the Query Engine, posted back in platform markup."""
    conversation, created = await resolve_conversation(user=user, text=text)

    try:
        # Messengers have no model picker, so neither sticky layer applies: the
        # turn always runs on the admin default (conversation.html#route).
        resolved = await qe_service.resolve_chat_model(
            ctx.session, requested=None, conversation_sticky=None, user_sticky=None
        )
    except ApiError as exc:
        await ctx.session.rollback()
        await say(profile.phrase(ctx.locale, "turn_failed", code=exc.code))
        return

    user_message = await store.append(
        ctx.session, conversation_id=conversation.id, role=MessageRole.USER, content=text
    )
    await ctx.session.commit()  # the words are durable before the model is dialled
    if created and on_conversation_persisted is not None:
        await on_conversation_persisted(conversation)

    context = qe_service.TurnContext(
        session=ctx.session,
        cache=ctx.redis.cache,
        crypto_key=ctx.crypto_key,
        user_id=user.id,
        user_locale=user.locale,
        conversation=conversation,
        conversation_created=created,
        user_message=user_message,
        resolved=resolved,
    )
    collected = await qe_service.collect_turn(context)
    if collected.error_code is not None:
        if collected.text:
            # The turn errored mid-stream but the partial answer was persisted
            # (and the web surface streamed it); deliver it here too so the
            # chat history matches what the user saw, then flag the cut-off.
            await say(
                profile.format_text(collected.text)
                + "\n\n"
                + profile.phrase(ctx.locale, "turn_cut_off")
            )
        else:
            await say(profile.phrase(ctx.locale, "turn_failed", code=collected.error_code))
        return
    if not collected.text:
        return
    reply = profile.format_text(collected.text)
    if collected.citations:
        reply += "\n\n" + profile.sources_block(
            collected.citations, heading=profile.phrase(ctx.locale, "sources")
        )
    await say(reply)
