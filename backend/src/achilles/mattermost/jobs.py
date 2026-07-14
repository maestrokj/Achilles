"""SAQ job: mattermost_inbound — one DM post through identity → dialogue → post-back.

The road is the shared messenger pipeline; this module contributes what
Mattermost dictates: thread = conversation keyed by root_id (Slack's rule —
threads exist even in a DM), standard-Markdown markup, and code-only linking —
a `posted` event names the author but carries no trustworthy email, so there is
no auto-link. Server membership grants nothing: the unlinked person gets a
hint, not an answer.
"""

import logging
from dataclasses import dataclass

import httpx
from saq.types import Context

from achilles.auth.models import User
from achilles.auth.security.crypto import decrypt
from achilles.config import settings as app_settings
from achilles.mattermost.client import MattermostApiError, MattermostClient
from achilles.mattermost.format import sources_block, to_markdown
from achilles.mattermost.phrases import phrase
from achilles.mattermost.service import get_settings
from achilles.messenger import pipeline
from achilles.query_engine.constants import Surface
from achilles.query_engine.conversation import store
from achilles.query_engine.models import Conversation

logger = logging.getLogger(__name__)

_PLATFORM = "mattermost"

PROFILE = pipeline.SurfaceProfile(
    platform=_PLATFORM,
    phrase=phrase,
    format_text=to_markdown,
    sources_block=sources_block,
)


@dataclass(frozen=True, slots=True)
class InboundDm:
    """One Mattermost DM post as the job receives it."""

    channel_id: str
    mm_user: str
    text: str
    post_id: str
    root_id: str | None

    @property
    def reply_root(self) -> str:
        # No nested threads: a reply must point at the true root — the inbound
        # post's own root if it sits in a thread, else the post itself.
        return self.root_id or self.post_id

    @property
    def is_thread_reply(self) -> bool:
        return self.root_id is not None


async def mattermost_inbound(
    ctx: Context,
    *,
    channel_id: str,
    mm_user: str,
    text: str,
    post_id: str,
    root_id: str | None = None,
) -> None:
    """One inbound DM: resolve who wrote, then link or answer in the thread."""
    del ctx
    dm = InboundDm(
        channel_id=channel_id, mm_user=mm_user, text=text, post_id=post_id, root_id=root_id
    )
    async with pipeline.job_connections(app_settings) as job:
        row = await get_settings(job.session)
        if not row.is_available or row.base_url is None or row.bot_token_enc is None:
            return  # switched off between event and pickup — stay silent
        client = MattermostClient(row.base_url, decrypt(row.bot_token_enc, key=job.crypto_key))
        try:

            async def say(message: str) -> None:
                try:
                    await client.create_post(
                        channel_id=dm.channel_id, message=message, root_id=dm.reply_root
                    )
                except (MattermostApiError, httpx.HTTPError) as exc:
                    # A refusal or a transport error to the server — the turn is
                    # already persisted, so swallow rather than fail (and retry) the job.
                    logger.warning("mattermost create_post failed: %s", exc)

            async def resolve_conversation(*, user: User, text: str) -> tuple[Conversation, bool]:
                # Thread = conversation (conversation.html#session-boundary):
                # a reply continues it, a root message starts a fresh one.
                meta: dict[str, object] = {"channel_id": dm.channel_id, "root_id": dm.reply_root}
                conversation = (
                    await store.find_by_meta(
                        job.session, user_id=user.id, surface=Surface.MATTERMOST, meta=meta
                    )
                    if dm.is_thread_reply
                    else None
                )
                if conversation is not None:
                    return conversation, False
                conversation = await store.create(
                    job.session,
                    user_id=user.id,
                    surface=Surface.MATTERMOST,
                    first_message=text,
                    meta=meta,
                )
                return conversation, True

            await pipeline.handle_inbound(
                job,
                say,
                profile=PROFILE,
                platform_user_id=dm.mm_user,
                text=dm.text,
                resolve_conversation=resolve_conversation,
            )
        finally:
            await client.aclose()
