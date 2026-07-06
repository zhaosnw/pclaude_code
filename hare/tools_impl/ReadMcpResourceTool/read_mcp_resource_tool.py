"""
ReadMcpResourceTool – read a specific MCP resource by URI.

Port of: src/tools/ReadMcpResourceTool/ReadMcpResourceTool.ts
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "ReadMcpResource"


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "MCP server name"},
            "uri": {"type": "string", "description": "Resource URI to read"},
        },
        "required": ["server", "uri"],
    }


async def call(server: str, uri: str, **kwargs: Any) -> dict[str, Any]:
    """Read an MCP resource by URI using the real pool's resources/read endpoint."""
    if not server or not uri:
        return {"error": "server and uri are required."}

    try:
        from hare.services.mcp.client import get_mcp_client_pool

        pool = get_mcp_client_pool()
        return await pool.read_resource(server, uri)
    except ImportError:
        return {"error": "MCP client pool not available."}
    except Exception as e:
        return {"error": f"Failed to read MCP resource: {e}"}
