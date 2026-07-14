"""Prompt AI routes: the two admin-editable layers (ai-behavior wireframe, admin-panel API).

The screen lives in Admin Panel ("Prompt AI"); the contract and the data are
owned here. Only GET and PATCH exist — the singleton is never created or
deleted over the wire (405 for the rest comes free).
"""

from fastapi import APIRouter, Request

from achilles.ai_foundation.routes import AiAdmin
from achilles.ai_foundation.schemas import PromptOut, PromptPatch
from achilles.ai_foundation.services import prompt
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession

router = APIRouter(prefix="/admin/ai-prompt", tags=["admin-ai"])


@router.get("")
async def get_prompt(user: AiAdmin, session: DbSession) -> PromptOut:
    del user
    return await prompt.get_effective(session)


@router.patch("")
async def patch_prompt(
    user: AiAdmin, request: Request, session: DbSession, body: PromptPatch
) -> PromptOut:
    result = await prompt.apply_patch(session, body, actor_id=user.id)
    await record_audit(
        request,
        action=AuditAction.AI_PROMPT_UPDATE,
        actor_id=user.id,
        target_type="prompt_settings",
        target_id="1",
        meta={"fields": sorted(body.model_fields_set)},
    )
    return result
