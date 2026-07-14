"""Lane `agents`: agent runs with their own LLM-call ceiling, isolated from chat.

Run: `saq achilles.infra.worker.agents.settings`.
The lane concurrency is the static ceiling of SAQ slots per process; the
live organization-wide limit is the DB gate at mark_running, driven by
platform_settings.agent_max_concurrency — a PATCH applies without a restart.
"""

from typing import Any

from achilles.agent_engine.jobs import run_agent
from achilles.infra.worker.base import Lane, lane_settings

settings: dict[str, Any] = lane_settings(Lane.AGENTS, functions=[run_agent])
