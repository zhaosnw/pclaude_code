from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hare.services.mcp.runtime import _McpRuntimeTool
from hare.services.mcp.runtime import connect_explicit_mcp_tools


class _FakePool:
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict):
        return {
            "content": [{"type": "text", "text": f"{server_name}:{tool_name}:{arguments['text']}"}],
            "is_error": False,
        }


def test_mcp_runtime_tool_scopes_name_schema_and_call() -> None:
    tool = _McpRuntimeTool(
        "echo",
        {
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
        _FakePool(),
    )

    assert tool.name == "mcp__echo__echo"
    assert tool.input_schema()["properties"]["text"]["type"] == "string"
    result = asyncio.run(tool.call({"text": "hello"}))
    assert result.data["content"][0]["text"] == "echo:echo:hello"


@pytest.mark.asyncio
async def test_connect_explicit_mcp_tools_discovers_echo_server(monkeypatch) -> None:
    seed_dir = Path(__file__).resolve().parents[1] / "hare" / "alignment" / "seeds"
    monkeypatch.chdir(seed_dir)
    tools, pool = await connect_explicit_mcp_tools(["mcp_echo_config.json"])
    try:
        assert [tool.name for tool in tools] == ["mcp__echo__echo"]
    finally:
        await pool.disconnect_all()
