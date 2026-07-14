"""The agent's tool belt: the locked KS core + the owner's optional picks.

Core (runtime.html#tools): search / graph / sql — read-only KS primitives
called under the owner's identity (never an argument — it comes from the
session context). With an empty base the core is not offered at all, uniform
with chat (hybrid-search.html#emptiness); optional catalog tools stay.

Parallel tool calls share one AsyncSession, which tolerates no concurrency —
the internal lock serializes DB work.
"""

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.agent_engine.models import AgentTool
from achilles.ai_foundation.llm.harness import HarnessTool
from achilles.ai_foundation.llm.types import ToolSpec
from achilles.ai_foundation.models import Tool
from achilles.ai_foundation.tools.binding import bind_catalog_tool
from achilles.knowledge_store.models import Entity
from achilles.knowledge_store.retrieval import aggregate, graph, hybrid
from achilles.knowledge_store.retrieval.evidence import fetch_evidence
from achilles.knowledge_store.retrieval.sql import FILTERS_JSON_SCHEMA, parse_filters
from achilles.knowledge_store.services import emptiness

SEARCH_TOP_K = 8
GRAPH_TOP_K = 20
GRAPH_MAX_DEPTH = 3

_EMPTY_ANSWER = "No matching records found in the knowledge base."

# Prompt-layer text — the model reads this, not the UI (i18n does not apply).

SEARCH_SPEC = ToolSpec(
    name="search",
    description=(
        "Search the company knowledge base (hybrid: semantic + keyword). Write a "
        "standalone query naming the subject explicitly. Results carry entity_id "
        "values you can pass to the graph tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Standalone search query"},
            "filters": FILTERS_JSON_SCHEMA,
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)

GRAPH_SPEC = ToolSpec(
    name="graph",
    description=(
        "Walk the knowledge graph from known records: pass entity_id values "
        "(from search results) to find related records up to `depth` hops away."
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
            "depth": {"type": "integer", "minimum": 1, "maximum": GRAPH_MAX_DEPTH, "default": 1},
        },
        "required": ["entity_ids"],
        "additionalProperties": False,
    },
)

SQL_SPEC = ToolSpec(
    name="sql",
    description=(
        "Count records in the knowledge base grouped by an axis — the engine "
        "computes the numbers. Use for 'how many / distribution' questions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "group_by": {"type": "string", "enum": sorted(aggregate.GROUP_BY_AXES)},
            "filters": FILTERS_JSON_SCHEMA,
        },
        "required": ["group_by"],
        "additionalProperties": False,
    },
)

# The wire copy of the locked core: /agents/options hands these names to the
# UI (locked pills, admin badges) — the frontend never hardcodes them.
CORE_TOOL_NAMES: tuple[str, ...] = (SEARCH_SPEC.name, GRAPH_SPEC.name, SQL_SPEC.name)


class KnowledgeCore:
    """The three locked KS handlers of one run, bound to the owner's identity."""

    def __init__(self, session: AsyncSession, *, user_id: int) -> None:
        self._session = session
        self._user_id = user_id
        self._lock = asyncio.Lock()

    async def search(self, *, query: str = "", filters: object = None) -> str:
        standalone = " ".join(str(query).split())
        if not standalone:
            return "Error: 'query' is required"
        async with self._lock:
            result = await hybrid.search(
                self._session,
                user_id=self._user_id,
                query=standalone,
                top_k=SEARCH_TOP_K,
                filters=parse_filters(filters),
            )
            evidence = await fetch_evidence(self._session, result.hits)
        blocks: list[str] = []
        for item in evidence:
            title = item.title or "(untitled)"
            lines = [f"entity_id={item.entity_id} · {title} ({item.source_type})"]
            if item.url:
                lines.append(item.url)
            if item.best_chunk_text:
                lines.append(item.best_chunk_text)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) if blocks else _EMPTY_ANSWER

    async def graph(self, *, entity_ids: object = None, depth: int = 1) -> str:
        ids = (
            [int(v) for v in entity_ids if isinstance(v, int | str)]
            if isinstance(entity_ids, list)
            else []
        )
        if not ids:
            return "Error: 'entity_ids' is required"
        bounded_depth = max(1, min(int(depth), GRAPH_MAX_DEPTH))
        async with self._lock:
            hits = await graph.search(
                self._session,
                user_id=self._user_id,
                start_ids=ids,
                depth=bounded_depth,
                top_k=GRAPH_TOP_K,
            )
            if not hits:
                return "No related records found."
            rows = await self._session.execute(
                sa.select(Entity.id, Entity.title, Entity.source_type).where(
                    Entity.id.in_([hit.entity_id for hit in hits])
                )
            )
        titles = {entity_id: (title, source_type) for entity_id, title, source_type in rows}
        lines = []
        for hit in hits:
            title, source_type = titles.get(hit.entity_id, ("(unknown)", "?"))
            lines.append(
                f"entity_id={hit.entity_id} · {title or '(untitled)'} "
                f"({source_type}) · depth={hit.depth}"
            )
        return "\n".join(lines)

    async def sql(self, *, group_by: str = "", filters: object = None) -> str:
        async with self._lock:
            try:
                rows = await aggregate.aggregate(
                    self._session,
                    user_id=self._user_id,
                    group_by=str(group_by),
                    filters=parse_filters(filters),
                )
            except ValueError as exc:
                return f"Error: {exc}"
        if not rows:
            return "No records match."
        return "\n".join(f"{bucket}: {total}" for bucket, total in rows)


async def build_agent_tools(
    session: AsyncSession, *, crypto_key: bytes, agent_id: int, user_id: int
) -> list[HarnessTool]:
    """KS core (unless the base is empty) + the owner's allowed optional tools."""
    tools: list[HarnessTool] = []
    if not await emptiness.is_empty(session):
        core = KnowledgeCore(session, user_id=user_id)
        tools += [
            HarnessTool(spec=SEARCH_SPEC, handler=core.search),
            HarnessTool(spec=GRAPH_SPEC, handler=core.graph),
            HarnessTool(spec=SQL_SPEC, handler=core.sql),
        ]
    rows = (
        await session.execute(
            sa.select(Tool)
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .where(AgentTool.agent_id == agent_id, Tool.agents_allowed)
            .order_by(Tool.id)
        )
    ).scalars()
    for row in rows:
        bound = bind_catalog_tool(row, crypto_key=crypto_key)
        if bound is not None:
            tools.append(bound)
    return tools
