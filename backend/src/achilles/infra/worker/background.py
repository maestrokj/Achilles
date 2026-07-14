"""Lane `background`: heavy and bulky jobs (sync, embedding, curation, backups, bulk email).

Run: `saq achilles.infra.worker.background.settings`.
"""

from typing import Any

from achilles.email.jobs import send_invite_email
from achilles.harvester.connectors.registry import discover_connectors
from achilles.harvester.jobs import run_probe, run_sync
from achilles.infra.worker.base import Lane, lane_settings
from achilles.knowledge_store.jobs import run_backup, run_curation, run_reembed, run_restore

# run_sync resolves connector types from the registry; the worker process has
# no API lifespan, so discovery happens at lane-module import (idempotent).
discover_connectors()

settings: dict[str, Any] = lane_settings(
    Lane.BACKGROUND,
    functions=[
        run_curation,
        run_backup,
        run_restore,
        run_sync,
        run_reembed,
        run_probe,
        send_invite_email,  # bulk-CSV rows: volume paced here, not in the request
    ],
)
