"""MCP — a tool provider for AI clients, not a dialogue (mcp/index.html).

One HTTP endpoint at /mcp (official SDK, streamable HTTP, stateless), one tool:
search_knowledge — findings + sources under the key owner's identity; the
client model does the synthesis. Auth is delegated to Auth (API key, OAuth is
v2); the platform kill-switch is platform_settings.mcp_enabled.
"""
