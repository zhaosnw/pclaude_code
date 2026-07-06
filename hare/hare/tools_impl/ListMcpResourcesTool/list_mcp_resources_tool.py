"""
ListMcpResourcesTool – list available MCP resources from servers.

Port of: src/tools/ListMcpResourcesTool/ListMcpResourcesTool.ts
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "ListMcpResources"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "Optional server name to filter by"},
        },
    }


async def call(server: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """List MCP resources from connected MCP servers using the real pool."""
    try:
        from hare.services.mcp.client import get_mcp_client_pool

        pool = get_mcp_client_pool()
        all_resources: list[dict[str, Any]] = []
        for name in pool.list_servers():
            if server and name != server:
                continue
            try:
                resources = await pool.list_resources(name)
            except Exception:
                resources = []
            for r in resources:
                r["_server"] = name
                all_resources.append(r)

        by_server: dict[str, list[dict[str, Any]]] = {}
        for r in all_resources:
            srv = r.pop("_server", "unknown")
            by_server.setdefault(srv, []).append(r)

        result = {"resources": all_resources, "count": len(all_resources), "servers": list(by_server.keys())}
        if server:
            result["server_filter"] = server
        return result
    except ImportError:
        return {"error": "MCP client pool not available."}
    except Exception as e:
        return {"error": f"Failed to list MCP resources: {e}"}
