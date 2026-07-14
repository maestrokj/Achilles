"""Tool type registry: self-registration + discovery (tool-catalog.html).

Types register with the @register_tool decorator at import time; discovery
walks the built-in package plus the `achilles.tools` entry-point group (the
canonical channel for pip-installed custom tools). Runs once per process —
a new tool type appears after an image rebuild, by design.
"""

import importlib
import logging
import pkgutil
from importlib.metadata import entry_points

from achilles.ai_foundation.constants import TOOL_ACCESS_V1
from achilles.ai_foundation.tools.base import BaseTool

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "achilles.tools"

_registry: dict[str, BaseTool] = {}


def register_tool(cls: type[BaseTool]) -> type[BaseTool]:
    tool = cls()
    name = tool.manifest.name
    if name in _registry:
        msg = f"duplicate tool type {name!r}"
        raise ValueError(msg)
    if tool.manifest.access not in TOOL_ACCESS_V1:
        # Write tools are a v2 concern (per-user OAuth); die loudly at import.
        msg = f"tool {name!r} declares access {tool.manifest.access!r}, v1 allows read_only"
        raise ValueError(msg)
    _registry[name] = tool
    return cls


def registered_tools() -> dict[str, BaseTool]:
    return dict(_registry)


def get_tool_type(name: str) -> BaseTool | None:
    return _registry.get(name)


def discover_tool_types() -> None:
    """Import everything that self-registers; idempotent, called at startup."""
    tools_pkg = importlib.import_module("achilles.ai_foundation.tools")
    for module in pkgutil.iter_modules(tools_pkg.__path__):
        importlib.import_module(f"{tools_pkg.__name__}.{module.name}")
    custom_pkg = importlib.import_module(f"{tools_pkg.__name__}.custom")
    for module in pkgutil.iter_modules(custom_pkg.__path__):
        importlib.import_module(f"{custom_pkg.__name__}.{module.name}")
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        try:
            entry_point.load()
        except Exception:
            logger.exception("tool entry point %s failed to load", entry_point.name)
