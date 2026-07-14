"""Tool instances over the type registry (tool-catalog.html).

GET merges two worlds: the live code registry (what exists) with the tools
table (how each instance is configured). A type without a row shows defaults;
a row without a type (package removed) still shows, flagged by its stored
state. Rows appear lazily — POST for a known type — and DELETE resets the
instance to defaults, never unregisters the type.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import (
    CODE_UNKNOWN_TOOL,
    CheckStatus,
    ToolAccess,
    ToolSource,
)
from achilles.ai_foundation.models import Tool
from achilles.ai_foundation.schemas import ToolCreate, ToolOut, ToolPatch
from achilles.ai_foundation.tools.base import BaseTool, ToolContext
from achilles.ai_foundation.tools.registry import get_tool_type, registered_tools
from achilles.api.problems import CODE_CONFLICT, CODE_NOT_FOUND, ApiError
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt, encrypt_optional


def _tool_out(row: Tool | None, tool_type: BaseTool | None, *, name: str) -> ToolOut:
    manifest = tool_type.manifest if tool_type else None
    return ToolOut(
        id=row.id if row else None,
        name=name,
        source=row.source if row else (manifest.source if manifest else ToolSource.CUSTOM),
        access=row.access if row else (manifest.access if manifest else ToolAccess.READ_ONLY),
        config=row.config if row else None,
        credential_is_set=bool(row.credential_enc) if row else False,
        needs_credential=manifest.needs_credential if manifest else False,
        chat_enabled=row.chat_enabled if row else False,
        agents_allowed=row.agents_allowed if row else False,
        status=CheckStatus(row.status) if row else CheckStatus.UNCHECKED,
        last_check_at=row.last_check_at if row else None,
        parameters=manifest.parameters if manifest else {},
    )


async def list_tools(session: AsyncSession) -> list[ToolOut]:
    rows = {row.name: row for row in (await session.execute(sa.select(Tool))).scalars()}
    types = registered_tools()
    names = sorted(types.keys() | rows.keys())
    return [_tool_out(rows.get(name), types.get(name), name=name) for name in names]


async def get_row(session: AsyncSession, tool_id: int) -> Tool:
    row = await session.get(Tool, tool_id)
    if row is None:
        raise ApiError(404, CODE_NOT_FOUND, "Not found", f"tool {tool_id} does not exist")
    return row


async def create_tool(session: AsyncSession, data: ToolCreate, *, key: bytes) -> ToolOut:
    tool_type = get_tool_type(data.name)
    if tool_type is None:
        raise ApiError(
            422,
            CODE_UNKNOWN_TOOL,
            "Unknown tool",
            f"{data.name!r} is not a registered tool type",
        )
    row = Tool(
        name=data.name,
        source=tool_type.manifest.source,
        access=tool_type.manifest.access,
        config=data.config,
        credential_enc=encrypt_optional(data.credential, key=key),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            409, CODE_CONFLICT, "Conflict", f"tool {data.name!r} already has an instance"
        ) from exc
    return _tool_out(row, tool_type, name=row.name)


async def patch_tool(
    session: AsyncSession, tool_id: int, data: ToolPatch, *, key: bytes
) -> ToolOut:
    row = await get_row(session, tool_id)
    fields = data.model_fields_set
    if "chat_enabled" in fields and data.chat_enabled is not None:
        row.chat_enabled = data.chat_enabled
    if "agents_allowed" in fields and data.agents_allowed is not None:
        row.agents_allowed = data.agents_allowed
    if "config" in fields:
        row.config = data.config
    if "credential" in fields:
        row.credential_enc = encrypt_optional(data.credential, key=key)
        row.status = CheckStatus.UNCHECKED
    await session.commit()
    return _tool_out(row, get_tool_type(row.name), name=row.name)


async def delete_tool(session: AsyncSession, tool_id: int) -> None:
    row = await get_row(session, tool_id)
    await session.delete(row)
    await session.commit()


async def probe_tool(session: AsyncSession, tool_id: int, *, key: bytes) -> Tool:
    row = await get_row(session, tool_id)
    tool_type = get_tool_type(row.name)
    if tool_type is None:
        raise ApiError(
            422,
            CODE_UNKNOWN_TOOL,
            "Unknown tool",
            f"{row.name!r} has no registered type in this build",
        )
    try:
        credential = decrypt(row.credential_enc, key=key) if row.credential_enc else None
    except CiphertextInvalidError:
        # Undecryptable secret → a failed check, not a 500 on the whole surface.
        ok = False
    else:
        context = ToolContext(config=row.config or {}, credential=credential)
        ok = (await tool_type.probe(context)).ok
    row.status = CheckStatus.ACTIVE if ok else CheckStatus.ERROR
    row.last_check_at = datetime.now(UTC)
    await session.commit()
    return row
