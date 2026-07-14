"""Admin knowledge ops: sources slice + async reindex / backup / restore.

Owner/Admin only (Permission.KNOWLEDGE_ADMIN); async ops answer 202 + run id,
a duplicate start dies on the Postgres single-flight lock → 409.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Request, status
from pydantic import ValidationError

from achilles.ai_foundation.services import embeddings_client
from achilles.api.background import publish_lane
from achilles.api.problems import CODE_NOT_FOUND, CODE_VALIDATION_ERROR, ApiError
from achilles.auth.constants import Permission
from achilles.auth.dependencies import CryptoKey, require
from achilles.auth.models import User
from achilles.auth.routes.common import record_audit
from achilles.auth.security.crypto import encrypt_optional
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.events.constants import Board
from achilles.events.publisher import publish_board
from achilles.infra.worker.base import Lane
from achilles.knowledge_store.constants import (
    BACKUP_LIST_LIMIT,
    CODE_MAINTENANCE,
    CODE_RUN_ALREADY_FINISHED,
    BackupState,
    CurationTrigger,
)
from achilles.knowledge_store.models import BackupSettings, BackupSnapshot, CurationRun, Source
from achilles.knowledge_store.repositories import entities as entities_repo
from achilles.knowledge_store.routes.common import KnowledgeAdmin
from achilles.knowledge_store.schemas import (
    BackupSettingsOut,
    BackupSettingsPatch,
    BackupSnapshotOut,
    CurationRunOut,
    CurationStatusOut,
    KnowledgeMetricsOut,
    ReembedProgressOut,
    RestoreRequest,
    RunStarted,
    SnapshotStarted,
    SourceSlice,
    SourcesOut,
)
from achilles.knowledge_store.services import (
    backup_schedule,
    backups,
    curation,
    emptiness,
    maintenance,
    metrics,
    platform,
)
from achilles.knowledge_store.services.backup_storage import S3Credentials, resolve_storage

router = APIRouter(prefix="/admin/knowledge", tags=["admin-knowledge"])

# Backup destination/credentials rewire where the company's data lands — Owner only.
SettingsOwner = Annotated[User, require(Permission.SETTINGS_MANAGE)]


@router.get("/sources")
async def list_sources(user: KnowledgeAdmin, session: DbSession) -> SourcesOut:
    """Per-source slice + the platform is_empty property (hybrid-search.html#emptiness)."""
    del user
    counts = await entities_repo.counts_by_source(session)
    # sync_runs is referenced by table name — the model lives across the module
    # boundary in harvester, which imports KS (never the other way around).
    last_sync_rows = await session.execute(
        sa.text(
            "SELECT source_id, max(finished_at) FROM sync_runs "
            "WHERE finished_at IS NOT NULL GROUP BY source_id"
        )
    )
    last_syncs: dict[int, datetime] = {int(sid): stamp for sid, stamp in last_sync_rows.all()}
    rows = (await session.execute(sa.select(Source).order_by(Source.id))).scalars().all()
    return SourcesOut(
        sources=[
            SourceSlice(
                id=row.id,
                name=row.name,
                connector_type=row.connector_type,
                state=row.state,
                entity_count=counts.get(row.id, (0, 0))[0],
                chunk_count=counts.get(row.id, (0, 0))[1],
                last_sync=last_syncs.get(row.id),
            )
            for row in rows
        ],
        is_empty=await emptiness.is_empty(session),
    )


@router.post("/reindex", status_code=status.HTTP_202_ACCEPTED)
async def start_reindex(user: KnowledgeAdmin, request: Request, session: DbSession) -> RunStarted:
    """Manual curation run: journal row (queued) + publish; runtime steps are stage 5."""
    run_id = await curation.start_run(session, trigger=str(CurationTrigger.MANUAL))
    await session.commit()
    await publish_board(request.state.redis.cache, Board.KNOWLEDGE)  # a queued row appeared
    await publish_lane(
        request, Lane.BACKGROUND, "run_curation", job_id=f"curation:{run_id}", run_id=run_id
    )
    await record_audit(
        request,
        action=AuditAction.KNOWLEDGE_REINDEX,
        actor_id=user.id,
        target_type="curation_run",
        target_id=str(run_id),
    )
    return RunStarted(run_id=run_id)


@router.post("/backup", status_code=status.HTTP_202_ACCEPTED)
async def start_backup(
    user: KnowledgeAdmin, request: Request, session: DbSession, key: CryptoKey
) -> SnapshotStarted:
    settings_row = await backups.get_settings(session)
    # Unconfigured/unsupported destination or broken credentials → 409 here,
    # before a snapshot row is journalled.
    resolve_storage(
        settings_row.destination_url,
        creds_json=backups.decrypted_creds(settings_row, key=key),
    )
    snapshot_id = await backups.start_snapshot(session)
    await session.commit()
    await publish_board(request.state.redis.cache, Board.KNOWLEDGE)  # a queued row appeared
    await publish_lane(
        request,
        Lane.BACKGROUND,
        "run_backup",
        job_id=f"backup:manual:{snapshot_id}",
        snapshot_id=snapshot_id,
    )
    await record_audit(
        request,
        action=AuditAction.KNOWLEDGE_BACKUP,
        actor_id=user.id,
        target_type="backup_snapshot",
        target_id=str(snapshot_id),
    )
    return SnapshotStarted(snapshot_id=snapshot_id)


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED)
async def start_restore(
    body: RestoreRequest, user: KnowledgeAdmin, request: Request, session: DbSession
) -> SnapshotStarted:
    """Full-DB overwrite under maintenance mode; only a succeeded snapshot restores."""
    snapshot = await session.get(BackupSnapshot, body.snapshot_id)
    if snapshot is None or snapshot.state != str(BackupState.SUCCEEDED):
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such snapshot")
    # The atomic claim is the restore single-flight lock: a concurrent request
    # loses SET NX and gets 409 — two pg_restore must never overlap.
    if not await maintenance.enter_maintenance(request.state.redis.durable):
        raise ApiError(
            409, CODE_MAINTENANCE, "Maintenance in progress", "A restore is already running."
        )
    try:
        # The uuid keeps the dedup key from outliving the claim: restoring the
        # same snapshot again later must publish again — serialization lives in
        # the maintenance claim above, not in the dedup key.
        await publish_lane(
            request,
            Lane.BACKGROUND,
            "run_restore",
            job_id=f"restore:{body.snapshot_id}:{uuid.uuid4().hex[:8]}",
            snapshot_id=body.snapshot_id,
        )
    except Exception:
        await maintenance.exit_maintenance(request.state.redis.durable)
        raise
    await record_audit(
        request,
        action=AuditAction.KNOWLEDGE_RESTORE,
        actor_id=user.id,
        target_type="backup_snapshot",
        target_id=str(body.snapshot_id),
    )
    return SnapshotStarted(snapshot_id=body.snapshot_id)


@router.get("/metrics")
async def knowledge_metrics(
    user: KnowledgeAdmin, session: DbSession, source_id: int | None = None
) -> KnowledgeMetricsOut:
    """The storage tiles; source_id narrows every counter to that source's contribution."""
    del user
    if source_id is not None and await session.get(Source, source_id) is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such source")
    data = await metrics.graph_metrics(session, source_id=source_id)
    return KnowledgeMetricsOut(
        entities=data.entities,
        chunks=data.chunks,
        edges=data.edges,
        pending_refs=data.pending_refs,
        vector_bytes=data.vector_bytes,
    )


def _run_out(run: CurationRun) -> CurationRunOut:
    return CurationRunOut(
        id=run.id,
        trigger=run.trigger,
        state=run.state,
        started_at=run.started_at,
        finished_at=run.finished_at,
        steps=run.steps,
        error=run.error,
        created_at=run.created_at,
        destructive_open=run.destructive_since is not None,
    )


@router.get("/curation")
async def curation_status(user: KnowledgeAdmin, session: DbSession) -> CurationStatusOut:
    """The grooming panel: active run, last outcome, next scheduled window."""
    del user
    active = await curation.active_run(session)
    reembed = None
    if active is not None and active.trigger == str(CurationTrigger.MODEL_CHANGE):
        # Resolve the assigned embedder once — both metrics below join 3 tables for it.
        assigned = await embeddings_client.resolve_assigned(session)
        model = assigned[0] if assigned else None
        progress = await metrics.reembed_progress(session, model=model) if model else None
        if progress is not None:
            from_model, to_model = await metrics.reembed_model_names(session, model=model)
            reembed = ReembedProgressOut(
                done=progress[0], total=progress[1], from_model=from_model, to_model=to_model
            )
    settings_row = await platform.get_platform_settings(session)
    fire = backup_schedule.next_fire(
        backup_schedule.WindowCadence.for_curation(settings_row),
        timezone=settings_row.timezone,
        now=datetime.now(UTC),
    )
    last = await curation.last_finished(session)
    return CurationStatusOut(
        active=_run_out(active) if active else None,
        reembed=reembed,
        last=_run_out(last) if last else None,
        next_scheduled=fire,
    )


@router.post("/curation/{run_id}/cancel")
async def cancel_curation(
    user: KnowledgeAdmin, request: Request, session: DbSession, run_id: int
) -> RunStarted:
    run = await session.get(CurationRun, run_id)
    if run is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not Found", "No such curation run")
    if not await curation.cancel(session, run_id):
        raise ApiError(
            409,
            CODE_RUN_ALREADY_FINISHED,
            "Run already finished",
            f"Run {run_id} is {run.state}; only queued/running runs cancel.",
        )
    await session.commit()
    await publish_board(request.state.redis.cache, Board.KNOWLEDGE)
    await record_audit(
        request,
        action=AuditAction.KNOWLEDGE_CURATION_CANCEL,
        actor_id=user.id,
        target_type="curation_run",
        target_id=str(run_id),
    )
    return RunStarted(run_id=run_id)


def _backup_settings_out(row: BackupSettings) -> BackupSettingsOut:
    return BackupSettingsOut(
        destination_url=row.destination_url,
        credential_is_set=row.destination_creds_enc is not None,
        frequency=row.frequency,
        weekday=row.weekday,
        time=row.time,
        retention_count=row.retention_count,
    )


@router.get("/backup-settings")
async def get_backup_settings(user: KnowledgeAdmin, session: DbSession) -> BackupSettingsOut:
    del user
    return _backup_settings_out(await backups.get_settings(session))


@router.patch("/backup-settings")
async def patch_backup_settings(
    body: BackupSettingsPatch,
    user: SettingsOwner,
    request: Request,
    session: DbSession,
    key: CryptoKey,
) -> BackupSettingsOut:
    """Owner-only: destination + credentials + window (weekly => weekday, as curation)."""
    row = await backups.get_settings(session)
    fields = body.model_fields_set
    if "destination_url" in fields:
        row.destination_url = body.destination_url
    if "credential" in fields and body.credential is not None:
        # Write-only secret contract: None = keep; "" = clear; text = re-encrypt.
        if body.credential:
            try:
                S3Credentials.model_validate_json(body.credential)
            except ValidationError as exc:
                raise ApiError(
                    422,
                    CODE_VALIDATION_ERROR,
                    "Validation failed",
                    "The access key must be a JSON object with access_key and "
                    "secret_key (optional: endpoint_url, region).",
                ) from exc
        row.destination_creds_enc = encrypt_optional(body.credential or None, key=key)
    if body.frequency is not None:
        row.frequency = str(body.frequency)
    if "weekday" in fields:
        row.weekday = body.weekday
    if body.time is not None:
        row.time = body.time
    if body.retention_count is not None:
        row.retention_count = body.retention_count
    # The merged-row cadence rule, shared with PATCH /admin/settings (curation window).
    row.weekday = backup_schedule.normalize_cadence(row.frequency, row.weekday, field="weekday")
    await session.commit()
    await record_audit(
        request,
        action=AuditAction.KNOWLEDGE_BACKUP_SETTINGS_UPDATE,
        actor_id=user.id,
        target_type="backup_settings",
        target_id="1",
    )
    return _backup_settings_out(row)


@router.get("/backups")
async def list_backups(user: KnowledgeAdmin, session: DbSession) -> list[BackupSnapshotOut]:
    """Latest snapshots for the "recent snapshots" cards (schedule-driven, no manual cap)."""
    del user
    rows = (
        (
            await session.execute(
                sa.select(BackupSnapshot)
                .order_by(BackupSnapshot.started_at.desc())
                .limit(BACKUP_LIST_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return [BackupSnapshotOut.model_validate(row) for row in rows]
