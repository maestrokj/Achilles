"""Board taxonomy and pub/sub channel names for the live-updates stream."""

from enum import StrEnum

from achilles.infra.redis import PREFIX_PUSH


class Board(StrEnum):
    """A live status board — the unit the client invalidates queries by."""

    HARVESTER = "harvester"  # sync runs + progress · admins
    KNOWLEDGE = "knowledge"  # curation · re-embed · backups · admins
    AGENTS = "agents"  # agent run journal · owner + admins


# Admin boards fan out on one channel each — access is decided at subscribe
# time by role, so there is no per-user copy to publish.
BOARD_CHANNEL = PREFIX_PUSH + "board:{board}"
# Agent runs are per-owner; admins watch the union on a separate channel.
AGENTS_USER_CHANNEL = PREFIX_PUSH + "board:agents:user:{user_id}"
AGENTS_ADMIN_CHANNEL = PREFIX_PUSH + "board:agents:admin"

# Trailing-edge coalescing per connection: a burst of publishes for one board
# emits one frame now and one when the gap expires — a hot worker loop can
# never make a client refetch faster than this.
BOARD_EMIT_MIN_SECONDS = 1.5
