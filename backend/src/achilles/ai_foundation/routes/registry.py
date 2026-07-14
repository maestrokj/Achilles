"""AI registry routes: providers · models · assignments (index.html#api).

Owner/Admin only (Permission.AI_ADMIN). Write endpoints leave an audit trace;
secrets never round-trip (see schemas.py).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status

from achilles.ai_foundation.constants import CheckStatus, EmbedderRuntimeState
from achilles.ai_foundation.routes import AiAdmin
from achilles.ai_foundation.schemas import (
    AssignmentsOut,
    AssignmentsPatch,
    CheckOut,
    DiscoveryOut,
    EmbedderAssignedOut,
    EmbedderRuntimeOut,
    EmbedderStatusOut,
    ModelCreate,
    ModelOut,
    ModelPatch,
    ProviderCheckConfig,
    ProviderCreate,
    ProviderOut,
    ProviderPatch,
)
from achilles.ai_foundation.services import discovery, embeddings_client, registry
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession
from achilles.knowledge_store.routes.common import (
    ensure_no_active_reembed,
    kick_embedding_refresh,
)
from achilles.notifications.api import dispatch_from_request

router = APIRouter(prefix="/admin/ai", tags=["admin-ai"])

# --- Providers ---


@router.get("/providers")
async def list_providers(user: AiAdmin, session: DbSession, key: CryptoKey) -> list[ProviderOut]:
    del user
    return [registry.provider_out(p, key=key) for p in await registry.list_providers(session)]


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    user: AiAdmin, request: Request, session: DbSession, key: CryptoKey, body: ProviderCreate
) -> ProviderOut:
    provider = await registry.create_provider(session, body, key=key)
    await record_audit(
        request,
        action=AuditAction.AI_PROVIDER_CREATE,
        actor_id=user.id,
        target_type="ai_provider",
        target_id=str(provider.id),
    )
    return registry.provider_out(provider, key=key)


@router.get("/providers/{provider_id}")
async def get_provider(
    user: AiAdmin, session: DbSession, key: CryptoKey, provider_id: int
) -> ProviderOut:
    del user
    return registry.provider_out(await registry.get_provider(session, provider_id), key=key)


@router.patch("/providers/{provider_id}")
async def patch_provider(
    user: AiAdmin,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    provider_id: int,
    body: ProviderPatch,
) -> ProviderOut:
    provider = await registry.patch_provider(session, provider_id, body, key=key)
    await record_audit(
        request,
        action=AuditAction.AI_PROVIDER_UPDATE,
        actor_id=user.id,
        target_type="ai_provider",
        target_id=str(provider_id),
    )
    return registry.provider_out(provider, key=key)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    user: AiAdmin, request: Request, session: DbSession, provider_id: int
) -> None:
    await registry.delete_provider(session, provider_id)
    await record_audit(
        request,
        action=AuditAction.AI_PROVIDER_DELETE,
        actor_id=user.id,
        target_type="ai_provider",
        target_id=str(provider_id),
    )


@router.get("/providers/{provider_id}/discovery")
async def discover_provider_models(
    user: AiAdmin, session: DbSession, key: CryptoKey, provider_id: int
) -> DiscoveryOut:
    del user
    provider = await registry.get_provider(session, provider_id)
    return DiscoveryOut(models=await discovery.discover_models(provider, key=key))


@router.post("/providers/check-config")
async def check_provider_config(
    user: AiAdmin, key: CryptoKey, body: ProviderCheckConfig
) -> CheckOut:
    """The /check verdict for an unsaved draft — nothing is written anywhere."""
    del user
    verdict = await registry.probe_config(body, key=key)
    return CheckOut(status=verdict, last_check_at=datetime.now(UTC))


@router.post("/providers/{provider_id}/check")
async def check_provider(
    user: AiAdmin, request: Request, session: DbSession, key: CryptoKey, provider_id: int
) -> CheckOut:
    del user
    provider = await registry.probe_provider(session, provider_id, key=key)
    if provider.status == CheckStatus.ERROR:
        await dispatch_from_request(
            request,
            session,
            event="system.provider_unavailable",
            source_ref=f"provider/{provider.id}",
            params={"provider_name": provider.name},
            dedup_key=f"provider:{provider.id}",
        )
    return CheckOut(status=provider.status, last_check_at=provider.last_check_at)  # type: ignore[arg-type]


# --- Models ---


@router.get("/models")
async def list_models(user: AiAdmin, session: DbSession) -> list[ModelOut]:
    del user
    return [registry.model_out(m) for m in await registry.list_models(session)]


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(
    user: AiAdmin, request: Request, session: DbSession, body: ModelCreate
) -> ModelOut:
    model = await registry.create_model(session, body)
    await record_audit(
        request,
        action=AuditAction.AI_MODEL_CREATE,
        actor_id=user.id,
        target_type="ai_model",
        target_id=str(model.id),
    )
    return registry.model_out(model)


@router.patch("/models/{model_pk}")
async def patch_model(
    user: AiAdmin, request: Request, session: DbSession, model_pk: int, body: ModelPatch
) -> ModelOut:
    model = await registry.patch_model(session, model_pk, body)
    await record_audit(
        request,
        action=AuditAction.AI_MODEL_UPDATE,
        actor_id=user.id,
        target_type="ai_model",
        target_id=str(model_pk),
    )
    return registry.model_out(model)


@router.delete("/models/{model_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(user: AiAdmin, request: Request, session: DbSession, model_pk: int) -> None:
    await registry.delete_model(session, model_pk)
    await record_audit(
        request,
        action=AuditAction.AI_MODEL_DELETE,
        actor_id=user.id,
        target_type="ai_model",
        target_id=str(model_pk),
    )


# --- Assignments ---


@router.get("/assignments")
async def get_assignments(user: AiAdmin, session: DbSession) -> AssignmentsOut:
    del user
    return await registry.get_assignments(session)


@router.patch("/assignments")
async def patch_assignments(
    user: AiAdmin, request: Request, session: DbSession, body: AssignmentsPatch
) -> AssignmentsOut:
    if "harvester_embedding" in body.model_fields_set:
        # Swapping the embedder mid-refresh is a 409, not a silent re-point.
        await ensure_no_active_reembed(session)
        if body.harvester_embedding is not None:
            # A model the runtime can't hold is rejected before anything commits.
            await embeddings_client.ensure_model_fits(session, body.harvester_embedding)
    result, warm_target, orphaned = await registry.patch_assignments(session, body)
    await record_audit(
        request,
        action=AuditAction.AI_ASSIGNMENT_CHANGE,
        actor_id=user.id,
        target_type="model_assignments",
        meta=body.model_dump(exclude_unset=True, mode="json"),
    )
    for agent_id, owner_id, agent_name in orphaned:
        # Their run gate just closed — tell each owner (agent.model_removed).
        await dispatch_from_request(
            request,
            session,
            event="agent.model_removed",
            target_user_id=owner_id,
            source_ref=f"agent/{agent_id}",
            params={"agent_name": agent_name},
        )
    if warm_target is not None:
        await embeddings_client.warm_assigned(session, warm_target)
        await kick_embedding_refresh(request, warm_target)
    return result


@router.get("/embedder")
async def embedder_status(user: AiAdmin, session: DbSession) -> EmbedderStatusOut:
    """Assigned embedding model + the runtime's live phase (Admin status chips)."""
    del user
    resolved = await embeddings_client.resolve_assigned(session)
    if resolved is None:
        return EmbedderStatusOut(assigned=None, runtime=None)
    model, provider = resolved
    assigned = EmbedderAssignedOut(
        model_pk=model.id, model_id=model.model_id, display_name=model.display_name
    )
    if not provider.is_system or not provider.base_url:
        # Cloud embedders have no load phase — nothing to poll.
        runtime = EmbedderRuntimeOut(state=EmbedderRuntimeState.EXTERNAL)
        return EmbedderStatusOut(assigned=assigned, runtime=runtime)
    status_ = await embeddings_client.runtime_status(provider.base_url)
    if status_ is None:
        runtime = EmbedderRuntimeOut(state=EmbedderRuntimeState.UNREACHABLE)
    else:
        try:
            state = EmbedderRuntimeState(status_.state_of(model.model_id))
        except ValueError:  # a newer runtime speaking a state we don't know yet
            state = EmbedderRuntimeState.UNREACHABLE
        runtime = EmbedderRuntimeOut(state=state, error=status_.error_of(model.model_id))
    return EmbedderStatusOut(assigned=assigned, runtime=runtime)
