"""Tools AI routes: catalog, instance config, health probe (index.html#api).

Owner/Admin only. Secrets are write-only: only the is_set flag answers.
"""

from fastapi import APIRouter, Request, status

from achilles.ai_foundation.routes import AiAdmin
from achilles.ai_foundation.schemas import CheckOut, ToolCreate, ToolOut, ToolPatch
from achilles.ai_foundation.services import tools_admin
from achilles.auth.dependencies import CryptoKey
from achilles.auth.routes.common import record_audit
from achilles.auth.services.audit import AuditAction
from achilles.db.dependencies import DbSession

router = APIRouter(prefix="/admin/ai/tools", tags=["admin-ai"])


@router.get("")
async def list_tools(user: AiAdmin, session: DbSession) -> list[ToolOut]:
    del user
    return await tools_admin.list_tools(session)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tool(
    user: AiAdmin, request: Request, session: DbSession, key: CryptoKey, body: ToolCreate
) -> ToolOut:
    created = await tools_admin.create_tool(session, body, key=key)
    await record_audit(
        request,
        action=AuditAction.AI_TOOL_CREATE,
        actor_id=user.id,
        target_type="tool",
        target_id=str(created.id),
    )
    return created


@router.patch("/{tool_id}")
async def patch_tool(
    user: AiAdmin,
    request: Request,
    session: DbSession,
    key: CryptoKey,
    tool_id: int,
    body: ToolPatch,
) -> ToolOut:
    patched = await tools_admin.patch_tool(session, tool_id, body, key=key)
    await record_audit(
        request,
        action=AuditAction.AI_TOOL_UPDATE,
        actor_id=user.id,
        target_type="tool",
        target_id=str(tool_id),
        meta={"fields": sorted(body.model_fields_set - {"credential"})},
    )
    return patched


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(user: AiAdmin, request: Request, session: DbSession, tool_id: int) -> None:
    await tools_admin.delete_tool(session, tool_id)
    await record_audit(
        request,
        action=AuditAction.AI_TOOL_DELETE,
        actor_id=user.id,
        target_type="tool",
        target_id=str(tool_id),
    )


@router.post("/{tool_id}/check")
async def check_tool(user: AiAdmin, session: DbSession, key: CryptoKey, tool_id: int) -> CheckOut:
    del user
    row = await tools_admin.probe_tool(session, tool_id, key=key)
    return CheckOut(status=row.status, last_check_at=row.last_check_at)  # type: ignore[arg-type]
