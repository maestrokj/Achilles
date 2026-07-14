"""The producing side: one call after a committed state change.

Publish points are run transitions and committed mid-run progress writes —
never pure heartbeat touches. Best-effort by contract: a broken bus costs one
delayed refresh and must not fail the worker's own transaction.
"""

import logging

from redis.asyncio import Redis

from achilles.events.constants import (
    AGENTS_ADMIN_CHANNEL,
    AGENTS_USER_CHANNEL,
    BOARD_CHANNEL,
    Board,
)

logger = logging.getLogger(__name__)


async def publish_board(redis_cache: Redis, board: Board, *, user_id: int | None = None) -> None:
    """Nudge every stream watching `board`; agents runs need the owner's id."""
    if board is Board.AGENTS:
        if user_id is None:
            raise ValueError("Board.AGENTS requires the owning user_id")
        channels = [AGENTS_USER_CHANNEL.format(user_id=user_id), AGENTS_ADMIN_CHANNEL]
    else:
        channels = [BOARD_CHANNEL.format(board=board)]
    for channel in channels:
        try:
            await redis_cache.publish(channel, "1")
        except Exception:  # the nudge is best-effort
            logger.warning("Board nudge on %s failed", channel, exc_info=True)
