"""Source control-layer ops: connector building, credential crypto, derived health.

The routes and the job share one connector factory so auth/scope wiring can't
drift between the probe path and the sync path.
"""

from datetime import UTC, datetime

from achilles.api.problems import ApiError
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt
from achilles.harvester.connectors.base import BaseConnector, Diagnosis
from achilles.harvester.connectors.http import Throttle
from achilles.harvester.connectors.registry import get_connector_type
from achilles.harvester.constants import CODE_UNKNOWN_CONNECTOR, SourceHealth, SyncState
from achilles.harvester.models import SyncRun
from achilles.knowledge_store.constants import ProbeStatus
from achilles.knowledge_store.models import Source


def connector_class(connector_type: str) -> type[BaseConnector]:
    cls = get_connector_type(connector_type)
    if cls is None:
        raise ApiError(
            422,
            CODE_UNKNOWN_CONNECTOR,
            "Unknown connector type",
            f"No connector registered for type {connector_type!r}.",
        )
    return cls


def decrypt_credential(source: Source, *, key: bytes) -> str:
    """Stored ciphertext → plaintext; undecryptable (key rotated) reads as unset."""
    if not source.credential_enc:
        return ""
    try:
        return decrypt(source.credential_enc, key=key)
    except CiphertextInvalidError:
        return ""


def create_connector(
    cls: type[BaseConnector],
    source: Source,
    *,
    credential: str,
    throttle: Throttle | None = None,
) -> BaseConnector:
    """cls.create(...) over a Source row — probe, catalog and sync share it."""
    return cls.create(
        base_url=source.base_url,
        credential=credential,
        throttle=throttle,
        scope_mode=source.scope_mode,
        scope_list=tuple(str(v) for v in source.scope_list),
        content_filters=dict(source.content_filters),
    )


def build_connector(
    source: Source, *, key: bytes, throttle: Throttle | None = None
) -> BaseConnector:
    """One factory for probe/catalog routes and the sync job."""
    return create_connector(
        connector_class(source.connector_type),
        source,
        credential=decrypt_credential(source, key=key),
        throttle=throttle,
    )


def derive_health(source: Source, active_run: SyncRun | None, last_run: SyncRun | None) -> str:
    """Observation axis, never stored (data-model.html#health-derived).

    queued → a run waits for the worker; syncing → a run is live; error → the
    last run failed or the probe is red; idle → nothing to report.
    """
    if active_run is not None:
        if active_run.state == str(SyncState.QUEUED):
            return str(SourceHealth.QUEUED)
        return str(SourceHealth.SYNCING)
    failed_run = last_run is not None and last_run.state == str(SyncState.FAILED)
    failed_probe = source.last_probe_status is not None and source.last_probe_status != str(
        ProbeStatus.OK
    )
    if failed_run or failed_probe:
        return str(SourceHealth.ERROR)
    return str(SourceHealth.IDLE)


def probe_status_of(*, ok_reachability: bool, ok_credentials: bool) -> str:
    """Stepped probe result → the stored last_probe_status vocabulary."""
    if not ok_reachability:
        return str(ProbeStatus.UNREACHABLE)
    if not ok_credentials:
        return str(ProbeStatus.AUTH_FAILED)
    return str(ProbeStatus.OK)


def probe_status_from(diagnosis: Diagnosis) -> str:
    """Positional step decode: steps[0] = reachability, steps[1] = credentials."""
    steps = diagnosis.steps
    ok_reach = not steps or steps[0].ok
    ok_creds = len(steps) < 2 or steps[1].ok
    return probe_status_of(ok_reachability=ok_reach, ok_credentials=ok_creds)


def stamp_probe(source: Source, status: str, *, now: datetime | None = None) -> None:
    source.last_probe_at = now or datetime.now(UTC)
    source.last_probe_status = status
