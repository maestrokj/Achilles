"""SAQ job: slack_inbound — one DM event through identity → dialogue → post-back.

The road is the shared messenger pipeline; this module contributes what Slack
dictates: thread = conversation, mrkdwn markup, and auto-link by the
workspace-provisioned email (opt-out — workspace membership alone grants
nothing, the unlinked person gets a hint, not an answer).
"""

import logging
from dataclasses import dataclass

import httpx
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.api.problems import ApiError
from achilles.auth.models import User
from achilles.auth.security.crypto import decrypt
from achilles.auth.services import messenger_link
from achilles.config import settings as app_settings
from achilles.messenger import pipeline
from achilles.query_engine.constants import Surface
from achilles.query_engine.conversation import store
from achilles.query_engine.models import Conversation
from achilles.slack.client import SlackApiError, SlackBotClient
from achilles.slack.format import sources_block, to_mrkdwn
from achilles.slack.phrases import phrase
from achilles.slack.service import get_settings

logger = logging.getLogger(__name__)

_PLATFORM = "slack"

PROFILE = pipeline.SurfaceProfile(
    platform=_PLATFORM,
    phrase=phrase,
    format_text=to_mrkdwn,
    sources_block=sources_block,
)


@dataclass(frozen=True, slots=True)
class InboundDm:
    """One Slack DM event as the job receives it."""

    team: str
    channel: str
    slack_user: str
    text: str
    ts: str
    thread_ts: str | None

    @property
    def reply_ts(self) -> str:
        # A root message starts the thread at its own ts.
        return self.thread_ts or self.ts

    @property
    def is_thread_reply(self) -> bool:
        return self.thread_ts is not None


async def slack_inbound(
    ctx: Context,
    *,
    team: str,
    channel: str,
    slack_user: str,
    text: str,
    ts: str,
    thread_ts: str | None = None,
) -> None:
    """One inbound DM: resolve who wrote, then link or answer in the thread."""
    del ctx
    dm = InboundDm(
        team=team, channel=channel, slack_user=slack_user, text=text, ts=ts, thread_ts=thread_ts
    )
    async with pipeline.job_connections(app_settings) as job:
        row = await get_settings(job.session)
        if not row.is_available or row.bot_token_enc is None:
            return  # switched off between ack and pickup — stay silent
        client = SlackBotClient(decrypt(row.bot_token_enc, key=job.crypto_key))
        try:

            async def say(message: str) -> None:
                try:
                    await client.post_message(
                        channel=dm.channel, text=message, thread_ts=dm.reply_ts
                    )
                except (SlackApiError, httpx.HTTPError) as exc:
                    # A refusal slug or a transport error to slack.com — the turn is
                    # already persisted, so swallow rather than fail (and retry) the job.
                    logger.warning("slack post_message failed: %s", exc)

            async def resolve_conversation(*, user: User, text: str) -> tuple[Conversation, bool]:
                # Thread = conversation (conversation.html#session-boundary):
                # a reply continues it, a root message starts a fresh one.
                meta: dict[str, object] = {
                    "team": dm.team,
                    "channel": dm.channel,
                    "thread_ts": dm.reply_ts,
                }
                conversation = (
                    await store.find_by_meta(
                        job.session, user_id=user.id, surface=Surface.SLACK, meta=meta
                    )
                    if dm.is_thread_reply
                    else None
                )
                if conversation is not None:
                    return conversation, False
                conversation = await store.create(
                    job.session,
                    user_id=user.id,
                    surface=Surface.SLACK,
                    first_message=text,
                    meta=meta,
                )
                return conversation, True

            async def auto_link() -> User | None:
                return await _auto_link(job.session, client, slack_user=dm.slack_user)

            await pipeline.handle_inbound(
                job,
                say,
                profile=PROFILE,
                platform_user_id=dm.slack_user,
                text=dm.text,
                resolve_conversation=resolve_conversation,
                auto_link=auto_link if row.auto_link_by_email else None,
            )
        finally:
            await client.aclose()


async def _auto_link(
    session: AsyncSession, client: SlackBotClient, *, slack_user: str
) -> User | None:
    """Link by the workspace-provisioned email matching an active account."""
    try:
        info = await client.users_info(slack_user)
    except SlackApiError, httpx.HTTPError:
        return None
    profile = info.get("user")
    email = None
    if isinstance(profile, dict):
        inner = profile.get("profile")
        if isinstance(inner, dict):
            email = inner.get("email")
    if not isinstance(email, str) or not email:
        return None
    try:
        user = await messenger_link.auto_link_by_email(
            session, platform=_PLATFORM, platform_user_id=slack_user, email=email
        )
    except ApiError:
        # A concurrent first DM won the UNIQUE race and linked the mapping;
        # adopt what it wrote instead of crashing this job.
        await session.rollback()
        _, user = await messenger_link.resolve_identity(
            session, platform=_PLATFORM, platform_user_id=slack_user
        )
        return user
    if user is None:
        return None
    await session.commit()
    return user
