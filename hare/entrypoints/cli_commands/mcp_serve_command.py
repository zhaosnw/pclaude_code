"""
CLI MCP serve command – expose tools as an MCP server.

Port of: src/entrypoints/cli/mcpServeCommand.ts
"""

from __future__ import annotations


async def run_mcp_serve_command(
    transport: str = "stdio",
    port: int | None = None,
) -> None:
    """Start as an MCP server. Stub."""
    print(f"MCP server starting with transport={transport}")
