"""Catalog row → HarnessTool: the one binding shared by chat and agents.

Registry lookup, credential decryption and context binding happen here so
both consumers skip a broken tool the same way (logged, never a 500).
"""

import functools
import logging

from achilles.ai_foundation.llm.harness import HarnessTool
from achilles.ai_foundation.llm.types import ToolSpec
from achilles.ai_foundation.models import Tool
from achilles.ai_foundation.tools.base import ToolContext
from achilles.ai_foundation.tools.registry import get_tool_type
from achilles.auth.security.crypto import CiphertextInvalidError, decrypt

logger = logging.getLogger(__name__)


def bind_catalog_tool(row: Tool, *, crypto_key: bytes) -> HarnessTool | None:
    """None → the row is unusable (no registered type / broken credential)."""
    tool_type = get_tool_type(row.name)
    if tool_type is None:
        logger.warning("tool %r has no registered type — skipped", row.name)
        return None
    try:
        credential = decrypt(row.credential_enc, key=crypto_key) if row.credential_enc else None
    except CiphertextInvalidError:
        logger.warning("tool %r credential cannot be decrypted — skipped", row.name)
        return None
    bound = ToolContext(config=row.config or {}, credential=credential)
    return HarnessTool(
        spec=ToolSpec.from_manifest(tool_type.manifest),
        handler=functools.partial(tool_type.call, bound),
    )
