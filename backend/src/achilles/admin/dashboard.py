"""GET /admin/dashboard: the overview aggregate (dashboard.html).

The dashboard computes nothing of its own — every tile is a cheap aggregate
over data whose home is another section; the frontend links each tile there.
"attention" carries v1-derivable signals only (the notification feed is stage
9): failing sources, DLQ tails, a failed backup, provider errors, the budget
threshold. Audit rows are included only for a caller holding AUDIT_READ.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel

from achilles.admin.dependencies import SettingsReader
from achilles.agent_engine.constants import AgentRunState
from achilles.agent_engine.models import Agent, AgentRun
from achilles.agent_engine.service import org_zone
from achilles.ai_foundation.constants import AiFunction, CheckStatus
from achilles.ai_foundation.models import AgentModel, AiProvider, ChatModel, ModelAssignment
from achilles.ai_foundation.services import usage_read
from achilles.api.serialization import UtcDateTime
from achilles.auth.constants import AuditResult, Permission, UserStatus, has_permission
from achilles.auth.models import AuditLog, InviteToken, User
from achilles.db.dependencies import DbSession
from achilles.email.models import SmtpSettings
from achilles.harvester.constants import SyncState
from achilles.harvester.models import DeadLetter, SyncRun
from achilles.knowledge_store.constants import BackupState, SourceState
from achilles.knowledge_store.models import BackupSnapshot, Source
from achilles.knowledge_store.services import curation, identity_bridge, metrics, platform
from achilles.mattermost.models import MattermostSettings
from achilles.slack.models import SlackSettings
from achilles.telegram.models import TelegramSettings

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])

AUDIT_TOP = 4
BUDGET_WARNING_SHARE = 0.8  # ≥80% of the monthly budget raises the attention row


class UsersTile(BaseModel):
    total: int
    pending_invites: int
    deactivated: int


class SourcesTile(BaseModel):
    total: int
    active: int
    paused: int
    disconnected: int
    failing: int  # sources whose latest finished run failed


class KnowledgeTile(BaseModel):
    entities: int
    chunks: int
    edges: int


class AgentsTile(BaseModel):
    total: int
    active: int
    paused: int
    failing: int  # agents whose latest run failed


class SpendTile(BaseModel):
    month_cost: Decimal | None  # None = nothing priced this month
    budget: Decimal | None
    alert_enabled: bool


class CurationTile(BaseModel):
    state: str
    trigger: str
    reembed_done: int | None
    reembed_total: int | None


class SyncTile(BaseModel):
    state: str  # the freshest sync run's state
    started_at: UtcDateTime | None  # None while a run is still queued
    entities: int | None  # entities_done of that run
    running: int  # sources with an in-flight (queued/running) run


class BackupTile(BaseModel):
    state: str
    started_at: UtcDateTime
    size_bytes: int | None


class AuditRow(BaseModel):
    action: str
    actor_email: str | None
    success: bool
    created_at: UtcDateTime


class AttentionItem(BaseModel):
    """A derived signal; the frontend maps `kind` to wording and the fix-it route."""

    severity: Literal["critical", "warning"]
    kind: Literal["source_failing", "dlq", "backup_failed", "provider_error", "budget"]
    subject: str | None  # source/provider name when the signal is per-object
    count: int | None
    source_id: int | None = None  # set for the per-source kinds (source_failing, dlq)


class TasksTile(BaseModel):
    pending_invites: int
    unmatched_identities: int


class SetupTile(BaseModel):
    """Configured-or-not facts behind the first-run checklist card.

    Sources are deliberately absent — the frontend derives that step from the
    sources tile it already has (total > 0).
    """

    email: bool  # SMTP is available (enabled + host/port/from present)
    surfaces: bool  # at least one messenger surface is available
    embedding: bool  # harvester_embedding assignment picked
    chat_models: bool  # the user chat picker offers at least one model
    agent_models: bool  # the agent allow-list offers at least one model


class DashboardOut(BaseModel):
    org_name: str
    timezone: str
    is_empty: bool
    users: UsersTile
    sources: SourcesTile
    knowledge: KnowledgeTile
    agents: AgentsTile
    spend: SpendTile
    last_sync: SyncTile | None  # the freshest sync run, if any
    curation: CurationTile | None  # the active run, if any
    last_backup: BackupTile | None
    audit: list[AuditRow] | None  # None = the caller lacks AUDIT_READ
    attention: list[AttentionItem]
    tasks: TasksTile
    setup: SetupTile


async def _setup_tile(session: DbSession) -> SetupTile:
    # Singleton settings rows: absent row = the module was never touched.
    smtp = await session.scalar(sa.select(SmtpSettings).limit(1))
    slack = await session.scalar(sa.select(SlackSettings).limit(1))
    telegram = await session.scalar(sa.select(TelegramSettings).limit(1))
    mattermost = await session.scalar(sa.select(MattermostSettings).limit(1))
    embedding_model_id, chat_count, agent_count = (
        await session.execute(
            sa.select(
                sa.select(ModelAssignment.model_id)
                .where(ModelAssignment.function == str(AiFunction.HARVESTER_EMBEDDING))
                .scalar_subquery(),
                sa.select(sa.func.count()).select_from(ChatModel).scalar_subquery(),
                sa.select(sa.func.count()).select_from(AgentModel).scalar_subquery(),
            )
        )
    ).one()
    return SetupTile(
        email=smtp is not None and smtp.is_available,
        surfaces=any(row is not None and row.is_available for row in (slack, telegram, mattermost)),
        embedding=embedding_model_id is not None,
        chat_models=int(chat_count) > 0,
        agent_models=int(agent_count) > 0,
    )


async def _users_tile(session: DbSession) -> UsersTile:
    now = datetime.now(UTC)
    total, deactivated, pending = (
        await session.execute(
            sa.select(
                sa.select(sa.func.count()).select_from(User).scalar_subquery(),
                sa.select(sa.func.count())
                .select_from(User)
                .where(User.status == str(UserStatus.DEACTIVATED))
                .scalar_subquery(),
                sa.select(sa.func.count())
                .select_from(InviteToken)
                .where(InviteToken.accepted_at.is_(None), InviteToken.expires_at > now)
                .scalar_subquery(),
            )
        )
    ).one()
    return UsersTile(total=int(total), pending_invites=int(pending), deactivated=int(deactivated))


def _sources_with_failed_last_run() -> sa.Select[tuple[int]]:
    """Source IDs whose most recent sync run (max id per source) ended failed."""
    latest = (
        sa.select(sa.func.max(SyncRun.id).label("run_id")).group_by(SyncRun.source_id).subquery()
    )
    return (
        sa.select(SyncRun.source_id)
        .join(latest, latest.c.run_id == SyncRun.id)
        .where(SyncRun.state == str(SyncState.FAILED))
    )


def _syncing_sources() -> sa.Select[tuple[int]]:
    """Source IDs whose most recent sync run is still queued or running."""
    latest = (
        sa.select(sa.func.max(SyncRun.id).label("run_id")).group_by(SyncRun.source_id).subquery()
    )
    return (
        sa.select(SyncRun.source_id)
        .join(latest, latest.c.run_id == SyncRun.id)
        .where(SyncRun.state.in_((str(SyncState.QUEUED), str(SyncState.RUNNING))))
    )


def _agents_with_failed_last_run() -> sa.Select[tuple[int]]:
    latest = (
        sa.select(sa.func.max(AgentRun.id).label("run_id")).group_by(AgentRun.agent_id).subquery()
    )
    return (
        sa.select(AgentRun.agent_id)
        .join(latest, latest.c.run_id == AgentRun.id)
        .where(AgentRun.state == str(AgentRunState.FAILED))
    )


async def _sources_tile(session: DbSession) -> tuple[SourcesTile, list[AttentionItem]]:
    # One pass over sources: state + the failing flag + the DLQ tail per source.
    dlq_counts = (
        sa.select(DeadLetter.source_id, sa.func.count().label("dlq"))
        .group_by(DeadLetter.source_id)
        .subquery()
    )
    rows = (
        await session.execute(
            sa.select(
                Source.id,
                Source.name,
                Source.state,
                Source.id.in_(_sources_with_failed_last_run()).label("failing"),
                sa.func.coalesce(dlq_counts.c.dlq, 0),
            )
            .outerjoin(dlq_counts, dlq_counts.c.source_id == Source.id)
            .order_by(Source.id)
        )
    ).all()

    by_state: dict[str, int] = {}
    for _, _, state, _, _ in rows:
        by_state[state] = by_state.get(state, 0) + 1
    failing_rows = [(source_id, name) for source_id, name, _, failing, _ in rows if failing]
    dlq_rows = [(source_id, name, dlq) for source_id, name, _, _, dlq in rows if dlq]

    attention = [
        AttentionItem(
            severity="critical",
            kind="source_failing",
            subject=name,
            count=None,
            source_id=source_id,
        )
        for source_id, name in failing_rows
    ]
    attention += [
        AttentionItem(
            severity="warning",
            kind="dlq",
            subject=name,
            count=int(count),
            source_id=source_id,
        )
        for source_id, name, count in sorted(dlq_rows, key=lambda row: row[1])
    ]
    tile = SourcesTile(
        total=len(rows),
        active=by_state.get(str(SourceState.ACTIVE), 0),
        paused=by_state.get(str(SourceState.PAUSED), 0),
        disconnected=by_state.get(str(SourceState.DISCONNECTED), 0),
        failing=len(failing_rows),
    )
    return tile, attention


async def _agents_tile(session: DbSession) -> AgentsTile:
    total, active, failing = (
        await session.execute(
            sa.select(
                sa.select(sa.func.count()).select_from(Agent).scalar_subquery(),
                sa.select(sa.func.count())
                .select_from(Agent)
                .where(Agent.enabled.is_(True), Agent.admin_paused.is_(False))
                .scalar_subquery(),
                sa.select(sa.func.count())
                .select_from(_agents_with_failed_last_run().subquery())
                .scalar_subquery(),
            )
        )
    ).one()
    return AgentsTile(
        total=int(total), active=int(active), paused=int(total - active), failing=int(failing)
    )


@router.get("/dashboard")
async def get_dashboard(user: SettingsReader, session: DbSession) -> DashboardOut:
    settings_row = await platform.get_platform_settings(session)
    now = datetime.now(UTC)
    org_tz = org_zone(settings_row)

    users_tile = await _users_tile(session)
    sources_tile, attention = await _sources_tile(session)
    graph = await metrics.graph_counts(session)
    agents_tile = await _agents_tile(session)

    month = await usage_read.usage_total(
        session, since_local_date=usage_read.month_start(now, org_tz)
    )
    month_cost = month.cost
    budget = settings_row.ai_monthly_budget
    spend = SpendTile(
        month_cost=month_cost, budget=budget, alert_enabled=settings_row.ai_budget_alert_enabled
    )
    if (
        budget is not None
        and month_cost is not None
        and month_cost >= budget * Decimal(str(BUDGET_WARNING_SHARE))
    ):
        attention.append(AttentionItem(severity="warning", kind="budget", subject=None, count=None))

    last_run = (
        await session.execute(
            sa.select(SyncRun.state, SyncRun.started_at, SyncRun.entities_done)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
    ).first()
    sync_tile = None
    if last_run is not None:
        running = (
            await session.scalar(
                sa.select(sa.func.count()).select_from(_syncing_sources().subquery())
            )
        ) or 0
        sync_tile = SyncTile(
            state=last_run.state,
            started_at=last_run.started_at,
            entities=last_run.entities_done,
            running=running,
        )

    active_run = await curation.active_run(session)
    curation_tile = None
    if active_run is not None:
        done, total = None, None
        progress = await metrics.reembed_progress(session)
        if progress is not None:
            done, total = progress
        curation_tile = CurationTile(
            state=active_run.state,
            trigger=active_run.trigger,
            reembed_done=done,
            reembed_total=total,
        )

    snapshot = (
        await session.execute(
            sa.select(BackupSnapshot.state, BackupSnapshot.started_at, BackupSnapshot.size_bytes)
            .order_by(BackupSnapshot.started_at.desc())
            .limit(1)
        )
    ).first()
    backup_tile = None
    if snapshot is not None:
        backup_tile = BackupTile(
            state=snapshot.state,
            started_at=snapshot.started_at,
            size_bytes=snapshot.size_bytes,
        )
        if snapshot.state == str(BackupState.FAILED):
            attention.append(
                AttentionItem(severity="critical", kind="backup_failed", subject=None, count=None)
            )

    provider_rows = (
        await session.execute(
            sa.select(AiProvider.name).where(AiProvider.status == str(CheckStatus.ERROR))
        )
    ).all()
    attention += [
        AttentionItem(severity="critical", kind="provider_error", subject=name, count=None)
        for (name,) in provider_rows
    ]

    audit_rows = None
    if has_permission(user.role, Permission.AUDIT_READ):
        rows = (
            await session.execute(
                sa.select(AuditLog, User.email)
                .outerjoin(User, User.id == AuditLog.actor_id)
                .order_by(AuditLog.id.desc())
                .limit(AUDIT_TOP)
            )
        ).all()
        audit_rows = [
            AuditRow(
                action=entry.action,
                actor_email=email,
                success=entry.result == str(AuditResult.SUCCESS),
                created_at=entry.created_at,
            )
            for entry, email in rows
        ]

    unmatched = (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(User)
            .where(~sa.exists(identity_bridge.linked_principals()))
        )
    ) or 0

    # critical first — severity is the sort key of the attention list.
    attention.sort(key=lambda item: item.severity != "critical")

    return DashboardOut(
        org_name=settings_row.org_name,
        timezone=settings_row.timezone,
        # The emptiness property is chunks == 0 by definition — derived from the
        # counters already fetched (hybrid-search.html#emptiness).
        is_empty=graph.chunks == 0,
        users=users_tile,
        sources=sources_tile,
        knowledge=KnowledgeTile(entities=graph.entities, chunks=graph.chunks, edges=graph.edges),
        agents=agents_tile,
        spend=spend,
        last_sync=sync_tile,
        curation=curation_tile,
        last_backup=backup_tile,
        audit=audit_rows,
        attention=attention,
        tasks=TasksTile(pending_invites=users_tile.pending_invites, unmatched_identities=unmatched),
        setup=await _setup_tile(session),
    )
