"""Dev-compose worker: all three lanes in one process (prod runs one service per lane).

Run: `python -m achilles.infra.worker.dev`.
"""

import asyncio
import logging

from saq import Worker

from achilles.infra.worker import agents, background, interactive

logger = logging.getLogger(__name__)


async def run_all_lanes() -> None:
    workers = [
        Worker(**interactive.settings),
        Worker(**background.settings),
        Worker(**agents.settings),
    ]
    logger.info("Dev worker: draining all three lanes in one process")
    await asyncio.gather(*(worker.start() for worker in workers))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_all_lanes())


if __name__ == "__main__":
    main()
