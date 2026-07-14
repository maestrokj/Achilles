"""Source management API (index.html#api, tests.html).

Owner/Admin only (Permission.KNOWLEDGE_ADMIN); async starts answer 202 + run
id, a duplicate start dies on the per-source Postgres lock → 409. Auto modes
(reconciliation, watchdog re-sync) are cron-only and not exposed here.
"""

import contextlib
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Request, status
from redis.exceptions import RedisError

from achilles.ai_foundation.services import embeddings_client
from achilles.api import API_V1
from achilles.api.background import publish_lane
from achilles.api.problems import CODE_NOT_FOUND, CODE_VALIDATION_ERROR, ApiError, rate_limited
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.security.crypto import decrypt, encrypt, encrypt_optional
from achilles.auth.security.tokens import generate_token
from achilles.auth.services.audit import AuditAction
from achilles.config import settings as app_settings
from achilles.db.dependencies import DbSession
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.harvester.connectors.registry import get_connector_type, registered_connectors
from achilles.harvester.constants import (
    CODE_CONFIRM_MISMATCH,
    CODE_WEBHOOK_NOT_SUPPORTED,
    CODE_WEBHOOK_SIGNATURE_INVALID,
    CODE_WEBHOOK_UNAVAILABLE,
    WEBHOOK_DEDUP_TTL_SECONDS,
    WEBHOOK_GRACE_TTL_SECONDS,
    WEBHOOK_RATE_LIMIT,
    WEBHOOK_RATE_WINDOW_SECONDS,
    SyncMode,
    SyncTrigger,
)
from achilles.harvester.models import SyncRun
from achilles.harvester.schemas import (
    CatalogOut,
    ConnectorTypeOut,
    DeadLetterOut,
    DeleteConfirm,
    DiagnosisOut,
    DiagnosisStepOut,
    FanOutStarted,
    HealthOut,
    LastRunOut,
    ProbeOut,
    ProbeRequest,
    ScopeItem,
    SourceCreate,
    SourceOut,
    SourcePatch,
    SyncRequest,
    SyncRunOut,
    SyncStarted,
    WebhookSecretOut,
)
from achilles.harvester.services import dead_letters, sync_runs, webhook_alert
from achilles.harvester.services import sources as sources_svc
from achilles.infra.lifecycle import run_duration_seconds
from achilles.infra.rate_limit import hit_sliding_window
from achilles.infra.redis import PREFIX_DEDUP, PREFIX_GRACE, PREFIX_RATE_LIMIT
from achilles.infra.worker.base import Lane
from achilles.knowledge_store.constants import (
    CODE_EMBEDDINGS_UNAVAILABLE,
    SourceState,
)
from achilles.knowledge_store.models import Source
from achilles.knowledge_store.routes.common import KnowledgeAdmin
from achilles.knowledge_store.services import entities as ks_entities
from achilles.notifications.api import dispatch_from_request

router = APIRouter(prefix="/sources", tags=["sources"])

_MANUAL_MODES = {str(SyncMode.INCREMENTAL), str(SyncMode.FULL)}

# The one place the inbound-webhook path lives: both the route below and the
# endpoint URL shown to admins derive from it, so they can never drift apart.
_WEBHOOK_ROUTE = "/harvester/webhooks/sources/{source_id}"


def _connector_supports_webhooks(connector_type: str) -> bool:
    cls = get_connector_type(connector_type)
    return bool(cls and cls.manifest.webhooks)


def webhook_endpoint_url(source_id: int) -> str:
    """The absolute address the source POSTs its events to (data-sources.html#webhooks)."""
    return app_settings.public_url(f"{API_V1}{_WEBHOOK_ROUTE.format(source_id=source_id)}")


async def _get_source(session: DbSession, source_id: int) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such source")
    return source


def _last_run_out(run: SyncRun | None) -> LastRunOut | None:
    if run is None:
        return None
    return LastRunOut(
        state=run.state,
        mode=run.mode,
        duration_seconds=run_duration_seconds(run.started_at, run.finished_at),
        error=run.error_detail,
        progress_done=run.entities_done,
        progress_total=run.entities_total,
    )


async def _source_out(
    session: DbSession, source: Source, *, entity_count: int | None = None
) -> SourceOut:
    active = await sync_runs.active_run(session, source.id)
    last = await sync_runs.last_finished(session, source.id)
    return SourceOut(
        id=source.id,
        name=source.name,
        connector_type=source.connector_type,
        state=source.state,
        health=sources_svc.derive_health(source, active, last),
        base_url=source.base_url,
        auth_account=source.auth_account,
        credential_is_set=bool(source.credential_enc),
        scope_mode=source.scope_mode,
        scope_list=[str(v) for v in source.scope_list],
        content_filters=dict(source.content_filters),
        sync_interval=source.sync_interval,
        reconcile_interval=source.reconcile_interval,
        reconcile_window=source.reconcile_window,
        authority_tier=source.authority_tier,
        incremental_cursor=source.incremental_cursor,
        last_probe_at=source.last_probe_at,
        last_probe_status=source.last_probe_status,
        last_sync_at=last.finished_at if last else None,
        last_run=_last_run_out(active or last),
        dlq_count=await dead_letters.count_for_source(session, source.id),
        entity_count=entity_count
        if entity_count is not None
        else await ks_entities.count_entities_for_source(session, source.id),
        webhook_supported=(supported := _connector_supports_webhooks(source.connector_type)),
        webhook_enabled=source.webhook_enabled,
        webhook_secret_set=bool(source.webhook_secret_enc),
        webhook_endpoint_url=webhook_endpoint_url(source.id) if supported else None,
        created_at=source.created_at,
    )


async def _start_and_publish(
    request: Request,
    session: DbSession,
    *,
    source_id: int,
    mode: str,
    trigger: str,
    scope: dict[str, object] | None = None,
) -> int:
    run_id = await sync_runs.start_run(
        session, source_id=source_id, mode=mode, trigger=trigger, scope=scope
    )
    await session.commit()
    await publish_board(request.state.redis.cache, Board.HARVESTER)  # a queued row appeared
    await publish_lane(request, Lane.BACKGROUND, "run_sync", job_id=f"sync:{run_id}", run_id=run_id)
    return run_id


@router.get("")
async def list_sources(user: KnowledgeAdmin, session: DbSession) -> list[SourceOut]:
    del user
    rows = (await session.execute(sa.select(Source).order_by(Source.id))).scalars().all()
    entity_counts = await ks_entities.entity_counts_by_source(session)
    return [
        await _source_out(session, row, entity_count=entity_counts.get(row.id, 0)) for row in rows
    ]


# Registered before /{source_id} so the literal path wins the match.
@router.post("/probe")
async def probe_draft(body: ProbeRequest, user: KnowledgeAdmin) -> ProbeOut:
    """Wizard step 3: probe a draft connection; on success step 4 gets the catalog.

    No source row exists yet — the connector is built from the unsaved draft.
    """
    del user
    cls = sources_svc.connector_class(body.connector_type)
    if cls.manifest.needs_base_url and not body.base_url:
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation failed", "This connector requires base_url."
        )
    connector = cls.create(base_url=body.base_url, credential=body.credential or "")
    try:
        diagnosis = await connector.check_connection()
        items = await connector.list_catalog() if diagnosis.ok else None
    finally:
        await connector.aclose()
    return ProbeOut(
        ok=diagnosis.ok,
        steps=[DiagnosisStepOut(name=s.name, ok=s.ok, detail=s.detail) for s in diagnosis.steps],
        catalog=None
        if items is None
        else [ScopeItem(native_id=i.native_id, name=i.name, kind=i.kind) for i in items],
    )


@router.get("/connectors")
async def list_connector_types(user: KnowledgeAdmin) -> list[ConnectorTypeOut]:
    """Wizard step 1: the open connector registry (custom types appear by themselves)."""
    del user
    return [
        ConnectorTypeOut(
            type=cls.manifest.type,
            title=cls.manifest.title,
            needs_base_url=cls.manifest.needs_base_url,
            credential_label=cls.manifest.credential_label,
            scope_kinds=list(cls.manifest.scope_kinds),
            collection_toggles=list(cls.manifest.collection_toggles),
            webhooks=cls.manifest.webhooks,
        )
        for _, cls in sorted(registered_connectors().items())
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_source(
    body: SourceCreate, user: KnowledgeAdmin, request: Request, session: DbSession, key: CryptoKey
) -> SourceOut:
    """Create + auto Full Sync (trigger=connect) — the connect wizard's final step.

    An assigned embedding model is a precondition of intake (pipeline.html#embed).
    """
    cls = sources_svc.connector_class(body.connector_type)
    if await embeddings_client.resolve_assigned(session) is None:
        raise ApiError(
            409,
            CODE_EMBEDDINGS_UNAVAILABLE,
            "No embedding model assigned",
            "Assign a harvester_embedding model before connecting sources.",
        )
    if cls.manifest.needs_base_url and not body.base_url:
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation failed", "This connector requires base_url."
        )
    source = Source(
        name=body.name,
        connector_type=body.connector_type,
        base_url=body.base_url,
        auth_account=str(body.auth_account),
        credential_enc=encrypt_optional(body.credential or None, key=key),
        scope_mode=str(body.scope_mode),
        scope_list=body.scope_list,
        content_filters=body.content_filters,
        sync_interval=body.sync_interval,
        reconcile_interval=body.reconcile_interval,
        reconcile_window=body.reconcile_window,
        authority_tier=str(body.authority_tier or cls.manifest.default_authority),
    )
    session.add(source)
    await session.flush()
    source_id = source.id
    await _start_and_publish(
        request,
        session,
        source_id=source_id,
        mode=str(SyncMode.FULL),
        trigger=str(SyncTrigger.CONNECT),
    )
    await record_audit(
        request,
        action=AuditAction.SOURCE_CREATE,
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
    )
    fresh = await session.get(Source, source_id)
    assert fresh is not None  # noqa: S101 — just created
    return await _source_out(session, fresh)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_all(user: KnowledgeAdmin, request: Request, session: DbSession) -> FanOutStarted:
    """Fan-out: incremental for every Active source without an active run."""
    rows = (
        (
            await session.execute(
                sa.select(Source).where(Source.state == str(SourceState.ACTIVE)).order_by(Source.id)
            )
        )
        .scalars()
        .all()
    )
    run_ids: list[int] = []
    for source in rows:
        if await sync_runs.active_run(session, source.id) is not None:
            continue
        try:
            run_id = await _start_and_publish(
                request,
                session,
                source_id=source.id,
                mode=str(SyncMode.INCREMENTAL),
                trigger=str(SyncTrigger.MANUAL),
            )
        except ApiError:
            # Lost the per-source lock to a concurrent start (scheduler tick):
            # skip this source, keep the fan-out going.
            await session.rollback()
            continue
        run_ids.append(run_id)
    await record_audit(
        request,
        action=AuditAction.SOURCE_SYNC_START,
        actor_id=user.id,
        target_type="source",
        target_id="*",
    )
    return FanOutStarted(run_ids=run_ids)


@router.get("/{source_id}")
async def get_source(user: KnowledgeAdmin, session: DbSession, source_id: int) -> SourceOut:
    del user
    return await _source_out(session, await _get_source(session, source_id))


@router.patch("/{source_id}")
async def patch_source(
    body: SourcePatch,
    user: KnowledgeAdmin,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    source_id: int,
) -> SourceOut:
    source = await _get_source(session, source_id)
    data = body.model_dump(exclude_unset=True)
    if "credential" in data:
        credential = data.pop("credential")
        # None = keep; "" = clear; text = re-encrypt (write-only secret contract).
        if credential is not None:
            source.credential_enc = encrypt_optional(credential or None, key=key)
    if "state" in data:
        state = data.pop("state")
        if state not in {str(SourceState.ACTIVE), str(SourceState.PAUSED)}:
            raise ApiError(
                422, CODE_VALIDATION_ERROR, "Validation failed", f"Unknown state {state!r}."
            )
        source.state = state
    if data.get("webhook_enabled") and source.webhook_secret_enc is None:
        # The real-time channel cannot authenticate anything without a secret —
        # generate one (rotate) before switching it on.
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation failed",
            "Generate a signing secret before enabling the webhook.",
        )
    for field_name, value in data.items():
        setattr(
            source,
            field_name,
            str(value)
            if field_name in ("scope_mode", "authority_tier") and value is not None
            else value,
        )
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SOURCE_UPDATE,
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
    )
    return await _source_out(session, source)


def _webhook_grace_key(source_id: int) -> str:
    """Redis key holding the previous (encrypted) secret during the rotation window."""
    return f"{PREFIX_GRACE}webhook:source:{source_id}"


@router.post("/{source_id}/webhook/rotate")
async def rotate_webhook_secret(
    user: KnowledgeAdmin,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    source_id: int,
) -> WebhookSecretOut:
    """Generate a fresh signing secret, shown once.

    The previous secret keeps verifying for a grace window so the cutover has
    no downtime while the admin updates the source by hand.
    """
    source = await _get_source(session, source_id)
    if not _connector_supports_webhooks(source.connector_type):
        raise ApiError(
            422,
            CODE_WEBHOOK_NOT_SUPPORTED,
            "Webhook not supported",
            "This connector type does not accept real-time events.",
        )
    # The outgoing secret keeps verifying deliveries in flight for the window,
    # stored encrypted (never plaintext at rest, even in Redis).
    if source.webhook_secret_enc is not None:
        await request.state.redis.durable.set(
            _webhook_grace_key(source_id),
            source.webhook_secret_enc,
            ex=WEBHOOK_GRACE_TTL_SECONDS,
        )
    secret = generate_token()
    source.webhook_secret_enc = encrypt(secret, key=key)
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SOURCE_UPDATE,
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
    )
    return WebhookSecretOut(secret=secret)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    body: DeleteConfirm,
    user: KnowledgeAdmin,
    request: Request,
    session: DbSession,
    source_id: int,
) -> None:
    """Config + data removal; type-to-confirm with the source name (tests.html)."""
    source = await _get_source(session, source_id)
    if body.confirm != source.name:
        raise ApiError(
            422,
            CODE_CONFIRM_MISMATCH,
            "Confirmation mismatch",
            "Type the exact source name to confirm deletion.",
        )
    await session.delete(source)  # FK CASCADE takes entities/chunks/ACL along
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.SOURCE_DELETE,
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
    )


@router.post("/{source_id}/test-connection")
async def test_connection(
    user: KnowledgeAdmin, session: DbSession, key: CryptoKey, source_id: int
) -> DiagnosisOut:
    """Stepped probe (sources.html): runs live, stamps last_probe_*."""
    del user
    source = await _get_source(session, source_id)
    connector = sources_svc.build_connector(source, key=key)
    try:
        diagnosis = await connector.check_connection()
    finally:
        await connector.aclose()
    sources_svc.stamp_probe(source, sources_svc.probe_status_from(diagnosis))
    await session.commit()
    return DiagnosisOut(
        ok=diagnosis.ok,
        steps=[DiagnosisStepOut(name=s.name, ok=s.ok, detail=s.detail) for s in diagnosis.steps],
    )


@router.get("/{source_id}/catalog")
async def get_catalog(
    user: KnowledgeAdmin, session: DbSession, key: CryptoKey, source_id: int
) -> CatalogOut:
    del user
    source = await _get_source(session, source_id)
    connector = sources_svc.build_connector(source, key=key)
    try:
        items = await connector.list_catalog()
    finally:
        await connector.aclose()
    return CatalogOut(
        items=[ScopeItem(native_id=i.native_id, name=i.name, kind=i.kind) for i in items]
    )


@router.post("/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def start_sync(
    body: SyncRequest, user: KnowledgeAdmin, request: Request, session: DbSession, source_id: int
) -> SyncStarted:
    """Manual run; a second start under the lock → 409 (single-flight)."""
    await _get_source(session, source_id)
    if body.mode not in _MANUAL_MODES:
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation failed", f"Mode {body.mode!r} is not manual."
        )
    run_id = await _start_and_publish(
        request, session, source_id=source_id, mode=body.mode, trigger=str(SyncTrigger.MANUAL)
    )
    await record_audit(
        request,
        action=AuditAction.SOURCE_SYNC_START,
        actor_id=user.id,
        target_type="sync_run",
        target_id=str(run_id),
    )
    return SyncStarted(run_id=run_id)


@router.post("/{source_id}/cancel")
async def cancel_sync(
    user: KnowledgeAdmin, request: Request, session: DbSession, source_id: int
) -> SyncStarted:
    await _get_source(session, source_id)
    active = await sync_runs.active_run(session, source_id)
    if active is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No active run to cancel")
    run_id = active.id
    await sync_runs.cancel(session, run_id)
    await session.commit()
    await publish_board(request.state.redis.cache, Board.HARVESTER)
    await record_audit(
        request,
        action=AuditAction.SOURCE_SYNC_CANCEL,
        actor_id=user.id,
        target_type="sync_run",
        target_id=str(run_id),
    )
    return SyncStarted(run_id=run_id)


@router.get("/{source_id}/runs")
async def list_runs(user: KnowledgeAdmin, session: DbSession, source_id: int) -> list[SyncRunOut]:
    del user
    await _get_source(session, source_id)
    return [
        SyncRunOut(
            id=run.id,
            mode=run.mode,
            trigger=run.trigger,
            state=run.state,
            entities_done=run.entities_done,
            entities_total=run.entities_total,
            error_count=run.error_count,
            error_detail=run.error_detail,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
        )
        for run in await sync_runs.list_runs(session, source_id)
    ]


@router.get("/{source_id}/dead-letters")
async def list_dead_letters(
    user: KnowledgeAdmin, session: DbSession, source_id: int
) -> list[DeadLetterOut]:
    del user
    await _get_source(session, source_id)
    return [
        DeadLetterOut(
            id=row.id,
            source_type=row.source_type,
            source_entity_id=row.source_entity_id,
            reason=row.reason,
            error_detail=row.error_detail,
            attempts=row.attempts,
            updated_at=row.updated_at,
        )
        for row in await dead_letters.list_for_source(session, source_id)
    ]


@router.post("/{source_id}/dead-letters/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_dead_letters(
    user: KnowledgeAdmin, request: Request, session: DbSession, source_id: int
) -> SyncStarted:
    """Targeted re-run of every queued item (sync-modes.html#dlq-retry)."""
    await _get_source(session, source_id)
    items = await dead_letters.items_for_source(session, source_id)
    if not items:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "The dead-letter queue is empty")
    run_id = await _start_and_publish(
        request,
        session,
        source_id=source_id,
        mode=str(SyncMode.INCREMENTAL),
        trigger=str(SyncTrigger.MANUAL),
        scope={"items": items},
    )
    await record_audit(
        request,
        action=AuditAction.SOURCE_SYNC_START,
        actor_id=user.id,
        target_type="sync_run",
        target_id=str(run_id),
    )
    return SyncStarted(run_id=run_id)


@router.get("/{source_id}/health")
async def get_health(user: KnowledgeAdmin, session: DbSession, source_id: int) -> HealthOut:
    """Cheap status endpoint for frequent polling (tests.html)."""
    del user
    source = await _get_source(session, source_id)
    active = await sync_runs.active_run(session, source_id)
    last = await sync_runs.last_finished(session, source_id)
    return HealthOut(
        health=sources_svc.derive_health(source, active, last),
        state=source.state,
        active_run_id=active.id if active else None,
        last_probe_status=source.last_probe_status,
        last_probe_at=source.last_probe_at,
    )


# --- Inbound webhook (anonymous, signature-gated) -------------------------
# A public POST endpoint the source calls on a change. The event is a trigger,
# never a data transport: once authenticated and de-duplicated it enqueues the
# same incremental pull a schedule would (security.html#webhooks, sources.html).

webhook_router = APIRouter(tags=["harvester-webhooks"])

_WEBHOOK_ACK: dict[str, object] = {}


async def _alert_webhook_rejection(request: Request, session: DbSession, source: Source) -> None:
    """A spike of rejected deliveries raises the Security alert, off the 401 path.

    Counts this rejection; only at the threshold does it dispatch — once per
    window, mirroring auth's alert_brute_force. Best-effort throughout: a Redis
    blip while counting, or a dispatch failure, must never turn the rejection
    into a 500 — the call still answers 401 Unauthorized.
    """
    try:
        count = await webhook_alert.record_rejection(request.state.redis.durable, source.id)
    except RedisError:
        return  # alerting is observability; never let it break the 401
    if webhook_alert.alert_due(count):
        await dispatch_from_request(
            request,
            session,
            event="security.webhook_rejected",
            source_ref=f"source/{source.id}",
            params={"source_name": source.name},
            dedup_key=f"hookrej:{source.id}",
        )


@webhook_router.post(_WEBHOOK_ROUTE)
async def receive_webhook(
    request: Request, session: DbSession, key: CryptoKey, source_id: int
) -> dict[str, object]:
    """Turn one inbound event into an incremental pull.

    Fail-closed: a bad signature is 401; anything not configured is a silent
    200 (no existence leak, no retry pile-up), mirroring the Slack surface.
    """
    source = await session.get(Source, source_id)
    if source is None or not source.webhook_enabled or source.webhook_secret_enc is None:
        return _WEBHOOK_ACK
    connector = get_connector_type(source.connector_type)
    if connector is None or not connector.manifest.webhooks:
        return _WEBHOOK_ACK

    raw_body = await request.body()
    now = datetime.now(UTC).timestamp()

    def verify(secret: str) -> str | None:
        return connector.verify_webhook(
            raw_body=raw_body, headers=request.headers, secret=secret, now=now
        )

    # The current secret verifies the steady state; only when it fails do we pay
    # a Redis read for the previous secret, still valid during a rotation window.
    delivery = verify(decrypt(source.webhook_secret_enc, key=key))
    if delivery is None:
        grace = await request.state.redis.durable.get(_webhook_grace_key(source_id))
        if grace is not None:
            delivery = verify(decrypt(grace, key=key))
    if delivery is None:
        await _alert_webhook_rejection(request, session, source)
        raise ApiError(
            401, CODE_WEBHOOK_SIGNATURE_INVALID, "Unauthorized", "signature check failed"
        )

    # Rate-limit only after the signature: the key is per (verified) source, so
    # pre-signature debiting would let an anonymous flood lock a real source out.
    try:
        decision = await hit_sliding_window(
            request.state.redis.durable,
            f"{PREFIX_RATE_LIMIT}hook:source:{source_id}",
            limit=WEBHOOK_RATE_LIMIT,
            window_seconds=WEBHOOK_RATE_WINDOW_SECONDS,
            now=now,
        )
    except RedisError as exc:
        raise ApiError(
            503, CODE_WEBHOOK_UNAVAILABLE, "Service Unavailable", "try again shortly"
        ) from exc
    if not decision.allowed:
        raise rate_limited(decision.retry_after, "Webhook rate limit exceeded")

    # One accepted delivery is a no-op if it repeats within the horizon (replay
    # / at-least-once retry). SET NX EX is the atomic first-seen test.
    dedup_key = f"{PREFIX_DEDUP}webhook:source:{source_id}:{delivery}"
    fresh = await request.state.redis.durable.set(
        dedup_key, "1", nx=True, ex=WEBHOOK_DEDUP_TTL_SECONDS
    )
    if not fresh:
        return _WEBHOOK_ACK

    # The event becomes a "pull the delta" task on the same incremental pipeline;
    # if a sync is already running it will pick the delta up, so do not pile on.
    if await sync_runs.active_run(session, source_id) is not None:
        return _WEBHOOK_ACK
    try:
        await _start_and_publish(
            request,
            session,
            source_id=source_id,
            mode=str(SyncMode.INCREMENTAL),
            trigger=str(SyncTrigger.WEBHOOK),
        )
    except ApiError:
        # Lost the per-source lock to a concurrent start — that run covers it.
        await session.rollback()
    except Exception:
        # The pull was never handed off (commit/publish failed). Drop the
        # first-seen key so the source's retry of this same delivery is honored
        # instead of being silently swallowed for the whole dedup horizon.
        with contextlib.suppress(RedisError):
            await request.state.redis.durable.delete(dedup_key)
        raise
    return _WEBHOOK_ACK
