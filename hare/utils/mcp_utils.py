"""
MCP utility functions.

Port of: src/services/mcp/utils.ts
"""

from __future__ import annotations

from typing import Any


def is_mcp_tool(tool: Any) -> bool:
    return getattr(tool, "is_mcp", False)


def get_mcp_server_name(tool: Any) -> str:
    return getattr(tool, "mcp_server_name", "")


def format_mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    if not name.startswith("mcp__"):
        return None
    parts = name[5:].split("__", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]
