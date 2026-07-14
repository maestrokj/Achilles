"""Registry invariants: providers, catalog, assignments (data-model.html).

The DB carries the hard locks (RESTRICT, CHECKs, the is_system trigger);
this layer turns them into clean 409/422 answers and adds the checks the
schema cannot express (type gating, disable-while-in-use, default swap).
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.models import Agent
from achilles.ai_foundation.constants import (
    CODE_EMBEDDING_DIM_MISMATCH,
    CODE_LAST_DEFAULT_PROTECTED,
    CODE_MODEL_IN_USE,
    CODE_MODEL_TYPE_MISMATCH,
    CODE_SYSTEM_PROVIDER_PROTECTED,
    EMBEDDING_DIM,
    AiFunction,
    CheckStatus,
    ModelType,
)
from achilles.ai_foundation.models import (
    AgentModel,
    AiModel,
    AiProvider,
    ChatModel,
    ModelAssignment,
)
from achilles.ai_foundation.schemas import (
    AssignmentsOut,
    AssignmentsPatch,
    ModelCreate,
    ModelListItem,
    ModelListOut,
    ModelListPatch,
    ModelOut,
    ModelPatch,
    ProviderCheckConfig,
    ProviderCreate,
    ProviderOut,
    ProviderPatch,
)
from achilles.ai_foundation.services import discovery
from achilles.api.problems import (
    CODE_CONFLICT,
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
    ApiError,
)
from achilles.auth.security.crypto import encrypt_optional, mask_encrypted

# --- Errors (shared shapes) ---


def _not_found(what: str, item_id: int) -> ApiError:
    return ApiError(404, CODE_NOT_FOUND, "Not found", f"{what} {item_id} does not exist")


def _model_in_use(model_id: int) -> ApiError:
    return ApiError(
        409,
        CODE_MODEL_IN_USE,
        "Model in use",
        f"model {model_id} is assigned to a function or listed for chat/agents",
    )


# --- Providers ---


def provider_out(provider: AiProvider, *, key: bytes) -> ProviderOut:
    mask = mask_encrypted(provider.api_key_enc, key=key)
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,  # type: ignore[arg-type]
        adapter=provider.adapter,  # type: ignore[arg-type]
        base_url=provider.base_url,
        api_key_mask=mask,
        is_system=provider.is_system,
        status=provider.status,  # type: ignore[arg-type]
        last_check_at=provider.last_check_at,
    )


async def list_providers(session: AsyncSession) -> list[AiProvider]:
    return list((await session.execute(sa.select(AiProvider).order_by(AiProvider.id))).scalars())


async def get_provider(session: AsyncSession, provider_id: int) -> AiProvider:
    provider = await session.get(AiProvider, provider_id)
    if provider is None:
        raise _not_found("provider", provider_id)
    return provider


async def create_provider(session: AsyncSession, data: ProviderCreate, *, key: bytes) -> AiProvider:
    provider = AiProvider(
        name=data.name,
        kind=data.kind,
        adapter=data.adapter,
        base_url=data.base_url,
        api_key_enc=encrypt_optional(data.api_key, key=key),
    )
    session.add(provider)
    try:
        await session.commit()
    except IntegrityError as exc:  # ck_ai_providers_base_url
        await session.rollback()
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation error", "local provider requires base_url"
        ) from exc
    return provider


async def patch_provider(
    session: AsyncSession, provider_id: int, data: ProviderPatch, *, key: bytes
) -> AiProvider:
    provider = await get_provider(session, provider_id)
    fields = data.model_fields_set
    if provider.is_system and fields & {"base_url", "api_key"}:
        # The system runtime's address and keylessness are platform-managed.
        raise ApiError(
            422,
            CODE_SYSTEM_PROVIDER_PROTECTED,
            "System provider protected",
            "only the display name of the system provider can change",
        )
    if "name" in fields and data.name is not None:
        provider.name = data.name
    if "base_url" in fields:
        provider.base_url = data.base_url
    if "api_key" in fields:
        provider.api_key_enc = encrypt_optional(data.api_key, key=key)
        provider.status = CheckStatus.UNCHECKED
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation error", "local provider requires base_url"
        ) from exc
    return provider


async def _probe_status(provider: AiProvider, *, key: bytes) -> CheckStatus:
    """The connectivity verdict: discovery call as a ping — ApiError → ERROR, else ACTIVE."""
    try:
        await discovery.discover_models(provider, key=key)
    except ApiError:
        return CheckStatus.ERROR
    return CheckStatus.ACTIVE


async def probe_config(data: ProviderCheckConfig, *, key: bytes) -> CheckStatus:
    """The /check verdict over an unsaved draft — same probe, no row, no status write."""
    draft = AiProvider(
        name="draft",
        kind=data.kind,
        adapter=data.adapter,
        base_url=data.base_url,
        api_key_enc=encrypt_optional(data.api_key, key=key),
    )
    return await _probe_status(draft, key=key)


async def probe_provider(session: AsyncSession, provider_id: int, *, key: bytes) -> AiProvider:
    """Connectivity probe: the discovery call as a ping; outcome lands in status."""
    provider = await get_provider(session, provider_id)
    provider.status = await _probe_status(provider, key=key)
    provider.last_check_at = datetime.now(UTC)
    await session.commit()
    return provider


async def delete_provider(session: AsyncSession, provider_id: int) -> None:
    provider = await get_provider(session, provider_id)
    if provider.is_system:
        raise ApiError(
            409,
            CODE_SYSTEM_PROVIDER_PROTECTED,
            "System provider protected",
            "the built-in Platform provider cannot be deleted",
        )
    model_ids = sa.select(AiModel.id).where(AiModel.provider_id == provider_id)
    if await _any_model_in_use(session, model_ids):
        raise ApiError(
            409,
            CODE_MODEL_IN_USE,
            "Model in use",
            f"provider {provider_id} has models assigned to a function or listed for chat/agents",
        )
    try:
        await session.delete(provider)
        await session.commit()
    except IntegrityError as exc:  # race backstop: RESTRICT fired on commit
        await session.rollback()
        raise _model_in_use(provider_id) from exc


# --- Models ---


def model_out(model: AiModel) -> ModelOut:
    return ModelOut(
        id=model.id,
        provider_id=model.provider_id,
        model_id=model.model_id,
        display_name=model.display_name,
        model_type=model.model_type,  # type: ignore[arg-type]
        origin=model.origin,
        is_enabled=model.is_enabled,
        price_input=model.price_input,
        price_output=model.price_output,
        meta=model.meta,
    )


async def list_models(session: AsyncSession) -> list[AiModel]:
    return list((await session.execute(sa.select(AiModel).order_by(AiModel.id))).scalars())


async def get_model(session: AsyncSession, model_id: int) -> AiModel:
    model = await session.get(AiModel, model_id)
    if model is None:
        raise _not_found("model", model_id)
    return model


async def create_model(session: AsyncSession, data: ModelCreate) -> AiModel:
    await get_provider(session, data.provider_id)  # 404 before the FK fires
    model = AiModel(
        provider_id=data.provider_id,
        model_id=data.model_id,
        display_name=data.display_name or data.model_id,
        model_type=data.model_type,
        origin=data.origin,
        is_enabled=True,  # adding by hand is an explicit act; enabled is the point
        price_input=data.price_input,
        price_output=data.price_output,
        meta=data.meta,
    )
    session.add(model)
    try:
        await session.commit()
    except IntegrityError as exc:  # uq_ai_models_provider_model
        await session.rollback()
        raise ApiError(
            409,
            CODE_CONFLICT,
            "Duplicate model",
            f"provider already lists model_id {data.model_id!r}",
        ) from exc
    return model


async def patch_model(session: AsyncSession, model_pk: int, data: ModelPatch) -> AiModel:
    model = await get_model(session, model_pk)
    fields = data.model_fields_set
    if (
        "is_enabled" in fields
        and data.is_enabled is False
        and await _is_model_in_use(session, model_pk)
    ):
        raise _model_in_use(model_pk)
    if (
        "model_type" in fields
        and data.model_type is not None
        and data.model_type != model.model_type
        # Re-typing a model in use would break the function/list type gates.
        and await _is_model_in_use(session, model_pk)
    ):
        raise _model_in_use(model_pk)
    if "model_type" in fields and data.model_type is not None:
        model.model_type = data.model_type
    if "display_name" in fields and data.display_name is not None:
        model.display_name = data.display_name
    if "is_enabled" in fields and data.is_enabled is not None:
        model.is_enabled = data.is_enabled
    if "price_input" in fields:
        model.price_input = data.price_input
    if "price_output" in fields:
        model.price_output = data.price_output
    if "meta" in fields and data.meta is not None:
        # Merge, not replace: editing one intrinsic (e.g. embedding_dim) must not
        # drop the others a model already carries (max_input_tokens, size).
        model.meta = {**(model.meta or {}), **data.meta}
    await session.commit()
    return model


async def delete_model(session: AsyncSession, model_pk: int) -> None:
    model = await get_model(session, model_pk)
    if await _is_model_in_use(session, model_pk):
        raise _model_in_use(model_pk)
    try:
        await session.delete(model)
        await session.commit()
    except IntegrityError as exc:  # race backstop
        await session.rollback()
        raise _model_in_use(model_pk) from exc


async def _is_model_in_use(session: AsyncSession, model_pk: int) -> bool:
    return await _any_model_in_use(session, sa.select(sa.literal(model_pk)))


async def _any_model_in_use(session: AsyncSession, model_ids: sa.Select[tuple[int]]) -> bool:
    ids = model_ids.subquery()
    query = sa.select(
        sa.exists(sa.select(1).where(ModelAssignment.model_id.in_(sa.select(ids))))
        | sa.exists(sa.select(1).where(ChatModel.model_id.in_(sa.select(ids))))
        | sa.exists(sa.select(1).where(AgentModel.model_id.in_(sa.select(ids))))
    )
    return bool((await session.execute(query)).scalar())


# --- Assignments ---

_FUNCTION_TYPES = {
    AiFunction.HARVESTER_EMBEDDING: ModelType.EMBEDDING,
}


async def get_assignments(session: AsyncSession) -> AssignmentsOut:
    rows = (await session.execute(sa.select(ModelAssignment))).scalars()
    by_function = {row.function: row.model_id for row in rows}
    return AssignmentsOut(
        harvester_embedding=by_function.get(AiFunction.HARVESTER_EMBEDDING),
        chat_models=await _list_out(session, ChatModel),
        agent_models=await _list_out(session, AgentModel),
        embedding_dim=EMBEDDING_DIM,
    )


async def patch_assignments(
    session: AsyncSession, patch: AssignmentsPatch
) -> tuple[AssignmentsOut, AiModel | None, list[tuple[int, int, str]]]:
    """Apply the whole board in one transaction.

    Returns the new state, the newly assigned harvester_embedding model (if
    that function changed) so the route can warm the runtime, and the agents
    orphaned by the change — replacing agent_models SET-NULLs `agents.model_id`,
    so (id, owner_id, name) of everyone whose run gate just closed.
    """
    fields = patch.model_fields_set
    warm_target: AiModel | None = None

    had_model: list[int] = []
    if patch.agent_models is not None:
        had_model = list(
            await session.scalars(sa.select(Agent.id).where(Agent.model_id.is_not(None)))
        )

    if "harvester_embedding" in fields:
        warm_target = await _set_function(
            session, AiFunction.HARVESTER_EMBEDDING, patch.harvester_embedding
        )
    if patch.chat_models is not None:
        await _replace_list(session, ChatModel, patch.chat_models)
    if patch.agent_models is not None:
        await _replace_list(session, AgentModel, patch.agent_models)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409,
            CODE_MODEL_IN_USE,
            "Model in use",
            "a removed list entry is still referenced",
        ) from exc

    orphaned: list[tuple[int, int, str]] = []
    if had_model:
        orphaned = [
            (agent_id, owner_id, name)
            for agent_id, owner_id, name in await session.execute(
                sa.select(Agent.id, Agent.user_id, Agent.name).where(
                    Agent.id.in_(had_model), Agent.model_id.is_(None)
                )
            )
        ]
    return await get_assignments(session), warm_target, orphaned


async def _checked_model(session: AsyncSession, model_pk: int, expected: ModelType) -> AiModel:
    model = await session.get(AiModel, model_pk)
    if model is None:
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation error", f"model {model_pk} does not exist"
        )
    if model.model_type != expected:
        raise ApiError(
            422,
            CODE_MODEL_TYPE_MISMATCH,
            "Model type mismatch",
            f"model {model_pk} is {model.model_type}, needs {expected}",
        )
    if not model.is_enabled:
        raise ApiError(
            422,
            CODE_VALIDATION_ERROR,
            "Validation error",
            f"model {model_pk} is disabled",
        )
    return model


async def _set_function(
    session: AsyncSession, function: AiFunction, model_pk: int | None
) -> AiModel | None:
    model = None
    if model_pk is not None:
        model = await _checked_model(session, model_pk, _FUNCTION_TYPES[function])
        if function is AiFunction.HARVESTER_EMBEDDING:
            _check_embedding_dim(model)
    row = (
        await session.execute(
            sa.select(ModelAssignment).where(ModelAssignment.function == function)
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(ModelAssignment(function=function, model_id=model_pk))
    else:
        row.model_id = model_pk
    return model


def _check_embedding_dim(model: AiModel) -> None:
    """chunks.embedding is halfvec(EMBEDDING_DIM) — a schema fact, not a preference.

    A model of another (or unreported) dimension cannot fill that column;
    a dimension change is a schema operation (lifecycle.html#embedding-refresh, v2).
    """
    dim = (model.meta or {}).get("embedding_dim")
    if dim != EMBEDDING_DIM:
        raise ApiError(
            409,
            CODE_EMBEDDING_DIM_MISMATCH,
            "Embedding dimension mismatch",
            f"the knowledge base stores {EMBEDDING_DIM}-dimensional vectors; "
            f"this model reports {dim!r}",
        )


async def _replace_list(
    session: AsyncSession, list_model: type[ChatModel | AgentModel], patch: ModelListPatch
) -> None:
    wanted: dict[int, bool] = {}  # ai_models.id → is_enabled; dedup, keep first
    for item in patch.items:
        wanted.setdefault(item.id, item.is_enabled)
    enabled = {model_pk for model_pk, on in wanted.items() if on}

    # The default is the model surfaces fall back to, so it must be a live one:
    # required whenever anything is enabled, and never a paused entry.
    if enabled and patch.default is None:
        raise ApiError(
            422,
            CODE_LAST_DEFAULT_PROTECTED,
            "Default required",
            "a list with an enabled model needs a default",
        )
    if patch.default is not None and patch.default not in enabled:
        raise ApiError(
            422, CODE_VALIDATION_ERROR, "Validation error", "default must be an enabled item"
        )
    for model_pk in wanted:
        await _checked_model(session, model_pk, ModelType.CHAT)

    existing = {
        row.model_id: row for row in (await session.execute(sa.select(list_model))).scalars()
    }
    for model_pk, row in existing.items():
        if model_pk not in wanted:
            await session.delete(row)
        elif row.is_default and model_pk != patch.default:
            row.is_default = False
    await session.flush()  # deletes + default drop land before the new default rises
    for model_pk, is_on in wanted.items():
        row = existing.get(model_pk)
        if row is None:
            session.add(
                list_model(
                    model_id=model_pk, is_default=model_pk == patch.default, is_enabled=is_on
                )
            )
        else:
            row.is_enabled = is_on
            if model_pk == patch.default:
                row.is_default = True


async def _list_out(
    session: AsyncSession, list_model: type[ChatModel | AgentModel]
) -> ModelListOut:
    rows = (await session.execute(sa.select(list_model).order_by(list_model.id))).scalars().all()
    default = next((row.model_id for row in rows if row.is_default), None)
    return ModelListOut(
        items=[ModelListItem(id=row.model_id, is_enabled=row.is_enabled) for row in rows],
        default=default,
    )
