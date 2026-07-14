"""Request-scope helpers shared by the knowledge routers (KS + Harvester)."""

import logging
from typing import Annotated

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.models import AiModel
from achilles.api.background import publish_lane
from achilles.api.problems import ApiError
from achilles.auth.constants import Permission
from achilles.auth.dependencies import require
from achilles.auth.models import User
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.worker.base import Lane
from achilles.knowledge_store.constants import CODE_REEMBED_IN_PROGRESS, CurationTrigger
from achilles.knowledge_store.services import curation, metrics

logger = logging.getLogger(__name__)

KnowledgeAdmin = Annotated[User, require(Permission.KNOWLEDGE_ADMIN)]


async def ensure_no_active_reembed(session: AsyncSession) -> None:
    """409 while an embedding refresh is running — no re-pointing the target mid-run.

    The re-embed signal is the active MODEL_CHANGE curation run, the same fact
    the grooming panel reads (lifecycle.html#embedding-refresh).
    """
    active = await curation.active_run(session)
    if active is not None and active.trigger == str(CurationTrigger.MODEL_CHANGE):
        raise ApiError(
            409,
            CODE_REEMBED_IN_PROGRESS,
            "Re-embedding in progress",
            "An embedding refresh is running — wait for it to finish or cancel it "
            "before changing the harvester_embedding model.",
        )


async def kick_embedding_refresh(request: Request, model: AiModel) -> None:
    """New harvester_embedding model → re-embed run (lifecycle.html#embedding-refresh).

    Best-effort in a session of its own: a 409 (another curation run holds the
    platform lock) must not fail the caller — the IS DISTINCT FROM predicate
    catches the stale rows on the next pass anyway.

    `model` is the just-assigned embedder from the caller's (uncommitted)
    transaction — the progress probe below must count staleness against it,
    not against whatever an independent session still sees assigned. A first
    assignment on an empty store has nothing to refresh: no run, no flash of
    "re-indexing" over zero chunks.
    """
    factory = request.state.db.pg_session_factory
    async with factory() as session:
        progress = await metrics.reembed_progress(session, model=model)
    if progress is None or progress[1] == 0 or progress[0] == progress[1]:
        logger.info("embedding refresh skipped: no chunks are stale against %s", model.model_id)
        return
    try:
        async with factory() as session, session.begin():
            run_id = await curation.start_run(session, trigger=str(CurationTrigger.MODEL_CHANGE))
    except ApiError:
        logger.info("embedding refresh not started: a curation run is already active")
        return
    await publish_board(request.state.redis.cache, Board.KNOWLEDGE)  # a queued run appeared
    await publish_lane(
        request, Lane.BACKGROUND, "run_reembed", job_id=f"reembed:{run_id}", run_id=run_id
    )
