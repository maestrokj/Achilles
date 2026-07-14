"""SAQ job: telegram_inbound — one DM update through identity → dialogue → reply.

The road is the shared messenger pipeline; this module contributes what the
surface dictates: there is no email, so the only link path is the one-time code
(no auto-link); and a DM has no threads, so the conversation is cut by the
`/new` command and the active one is held by a pointer in Redis
(telegram/index.html#conversation). Knowing the bot grants nothing — the
unlinked person gets a hint, not an answer.
"""

import logging

import httpx
from saq.types import Context

from achilles.api.problems import ApiError
from achilles.auth.models import User
from achilles.auth.security.crypto import decrypt
from achilles.config import settings as app_settings
from achilles.messenger import pipeline
from achilles.query_engine.constants import Surface
from achilles.query_engine.conversation import store
from achilles.query_engine.models import Conversation
from achilles.telegram.client import TelegramApiError, TelegramBotClient
from achilles.telegram.constants import (
    ACTIVE_CONV_KEY,
    ACTIVE_CONV_TTL_SECONDS,
    NEW_CONVERSATION_COMMAND,
)
from achilles.telegram.format import sources_block, to_html
from achilles.telegram.phrases import phrase
from achilles.telegram.service import get_settings

logger = logging.getLogger(__name__)

_PLATFORM = "telegram"

PROFILE = pipeline.SurfaceProfile(
    platform=_PLATFORM,
    phrase=phrase,
    format_text=to_html,
    sources_block=sources_block,
)


async def telegram_inbound(
    ctx: Context,
    *,
    chat_id: str,
    tg_user: str,
    text: str,
) -> None:
    """One inbound DM: resolve who wrote, then link, reset, or answer."""
    del ctx
    async with pipeline.job_connections(app_settings) as job:
        row = await get_settings(job.session)
        if not row.is_available or row.bot_token_enc is None:
            return  # switched off between ack and pickup — stay silent
        client = TelegramBotClient(decrypt(row.bot_token_enc, key=job.crypto_key))
        try:

            async def say(message: str) -> None:
                try:
                    await client.send_message(chat_id=chat_id, text=message)
                except (TelegramApiError, httpx.HTTPError) as exc:
                    # A refusal or a transport error to telegram.org — the turn is
                    # already persisted, so swallow rather than fail (and retry) the job.
                    logger.warning("telegram send_message failed: %s", exc)

            async def command(raw: str) -> str | None:
                if raw.strip() != NEW_CONVERSATION_COMMAND:
                    return None
                # An adapter command: reset the pointer, never enter it into history.
                await job.redis.cache.delete(ACTIVE_CONV_KEY.format(chat_id=chat_id))
                return phrase(job.locale, "new_conversation")

            async def resolve_conversation(*, user: User, text: str) -> tuple[Conversation, bool]:
                # No threads: a Redis pointer holds the active conversation. A missing
                # pointer (evicted, or just after `/new`) starts a fresh one.
                raw = await job.redis.cache.get(ACTIVE_CONV_KEY.format(chat_id=chat_id))
                if raw is not None:
                    try:
                        conversation = await store.get_owned(
                            job.session, conversation_id=int(raw), user_id=user.id
                        )
                    except ApiError, ValueError:
                        pass  # stale, foreign or malformed pointer — fall through to a fresh one
                    else:
                        return conversation, False
                conversation = await store.create(
                    job.session,
                    user_id=user.id,
                    surface=Surface.TELEGRAM,
                    first_message=text,
                    meta={"chat_id": chat_id},
                )
                return conversation, True

            async def on_conversation_persisted(conversation: Conversation) -> None:
                # Written only once the first user message is durable.
                await job.redis.cache.set(
                    ACTIVE_CONV_KEY.format(chat_id=chat_id),
                    str(conversation.id),
                    ex=ACTIVE_CONV_TTL_SECONDS,
                )

            await pipeline.handle_inbound(
                job,
                say,
                profile=PROFILE,
                platform_user_id=tg_user,
                text=text,
                resolve_conversation=resolve_conversation,
                on_conversation_persisted=on_conversation_persisted,
                command=command,
            )
        finally:
            await client.aclose()
