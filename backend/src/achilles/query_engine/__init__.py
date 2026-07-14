"""Query Engine: the dialogue orchestrator (docs/architecture/modules/query-engine/).

The user-selected chat model leads the turn and holds search_knowledge; the
module wires conversation persistence, the RAG route behind that tool, the
context budget and the SSE stream — one round of tools, then the answer.
"""
