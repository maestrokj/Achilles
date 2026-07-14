"""Tool type contract: manifest + call + probe (tool-catalog.html).

A tool *type* is a class in the code registry; a tool *instance* is a row in
the `tools` table joined by name. The manifest is the source of the wire
contract (name, parameters, access) — the prompt layer reads it from here,
never duplicates it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from achilles.ai_foundation.constants import ToolAccess, ToolSource


@dataclass(frozen=True, slots=True)
class ToolManifest:
    name: str  # registry key = tools.name
    access: ToolAccess  # v1 ships read_only only
    parameters: dict[str, Any]  # JSON Schema of call() arguments
    description: str = ""  # prompt-layer text — the model reads it, not the UI
    needs_credential: bool = False
    source: ToolSource = ToolSource.CUSTOM  # platform-shipped types say "preset"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Instance state a call/probe runs with: row config + decrypted secret."""

    config: dict[str, Any] = field(default_factory=dict)
    credential: str | None = None


class BaseTool(ABC):
    """One registered tool type; stateless — instance state comes as ToolContext."""

    manifest: ToolManifest

    @abstractmethod
    async def call(self, context: ToolContext, **arguments: object) -> object:
        """Execute the tool.

        Dispatched by the tool-calling harness (llm/harness.py): the caller
        binds ToolContext and hands the bound coroutine to HarnessTool. The
        chat turn and the agent loop join the same way (tools/binding.py).
        """

    @abstractmethod
    async def probe(self, context: ToolContext) -> ProbeResult:
        """Light connectivity ping — auth/reachability, not inference."""
