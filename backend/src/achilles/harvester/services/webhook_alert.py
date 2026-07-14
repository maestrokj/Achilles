"""Rejected-webhook spike alert (security.html#webhooks).

The Security notification raised when a source's inbound calls keep failing the
signature check. A fixed-window counter on redis-durable mirrors the brute-force
alert hook: the count crosses the threshold once per window, so it fires once
rather than on every rejected call. The route owns the dispatch (this context
has no queue plumbing), exactly as auth's alert_brute_force does.
"""

from redis.asyncio import Redis

from achilles.harvester.constants import (
    WEBHOOK_REJECT_ALERT_THRESHOLD,
    WEBHOOK_REJECT_ALERT_WINDOW_SECONDS,
)
from achilles.infra.redis import PREFIX_RATE_LIMIT

# Same family as the post-signature throttle key (rl:hook:source:{id}); this one
# counts the rejected calls that never made it past the signature.
_KEY = PREFIX_RATE_LIMIT + "hookrej:source:{source_id}"


async def record_rejection(redis: Redis, source_id: int) -> int:
    """Count one rejected delivery in a fixed window. Returns the count.

    The window is armed on the first rejection and NOT extended by later ones,
    so the count is "rejections since the window opened" and resets cleanly when
    it lapses — a fixed bucket, not a sliding TTL. That reset is what lets a
    sustained spike re-cross the threshold on the next bucket; the event's 1h
    dedup then keeps those re-alerts to an hourly reminder.
    """
    key = _KEY.format(source_id=source_id)
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, WEBHOOK_REJECT_ALERT_WINDOW_SECONDS)
    return count


def alert_due(count: int) -> bool:
    """True exactly once per window — at the alert threshold."""
    return count == WEBHOOK_REJECT_ALERT_THRESHOLD
