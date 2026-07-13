"""Runtime assembly for explicit CLI MCP servers."""

from __future__ import annotations

from typing import Any

from hare.tool import ToolBase, ToolResult
from hare.services.mcp.client import McpClientPool, get_mcp_client_pool
from hare.services.mcp.config import load_explicit_mcp_config_files


class _McpRuntimeTool(ToolBase):
    _annotations: dict[str, Any]

    def __init__(self, server_name: str, raw: dict[str, Any], pool: McpClientPool) -> None:
        self.server_name = server_name
        self.tool_name = str(raw["name"])
        self.name = f"mcp__{server_name}__{self.tool_name}"
        self.search_hint = str(raw.get("description", self.name))
        self._schema = raw.get("inputSchema", raw.get("input_schema", {"type": "object"}))
        self._pool = pool
        self._annotations = raw.get("annotations", {})
        self.mcp_info = type("McpInfo", (), {"server_name": server_name})()

    def input_schema(self) -> dict[str, Any]:
        return self._schema

    def is_read_only(self, _input: dict[str, Any]) -> bool:
        return bool(raw_annotations(self).get("readOnlyHint", False))

    async def call(self, args: dict[str, Any], **_kwargs: Any) -> ToolResult:
        result = await self._pool.call_tool(self.server_name, self.tool_name, args)
        return ToolResult(data=result)


def raw_annotations(tool: _McpRuntimeTool) -> dict[str, Any]:
    return getattr(tool, "_annotations", {})


async def connect_explicit_mcp_tools(paths: list[str]) -> tuple[list[ToolBase], McpClientPool]:
    """Connect CLI-configured servers and expose each remote tool to the query loop."""
    pool = get_mcp_client_pool()
    tools: list[ToolBase] = []
    for server in load_explicit_mcp_config_files(paths):
        if not server.enabled:
            continue
        connected = await pool.connect(server.name, server.config)
        if not connected.is_connected:
            continue
        for raw in await pool.list_tools(server.name):
            if isinstance(raw.get("name"), str) and raw["name"]:
                tool = _McpRuntimeTool(server.name, raw, pool)
                tool._annotations = raw.get("annotations", {})
                tools.append(tool)
    return tools, pool
