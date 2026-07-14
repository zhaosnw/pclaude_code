"""Unit tests for MCP client connection management and JSON-RPC layer."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from hare.services.mcp.client import (
    MCPError,
    get_mcp_client_pool,
    reset_mcp_client_pool,
)


@pytest.fixture(autouse=True)
def _reset_pool() -> None:
    reset_mcp_client_pool()
    yield
    reset_mcp_client_pool()


# ---------------------------------------------------------------------------
# Singleton pool
# ---------------------------------------------------------------------------


def test_get_mcp_client_pool_returns_singleton() -> None:
    p1 = get_mcp_client_pool()
    p2 = get_mcp_client_pool()
    assert p1 is p2


def test_reset_mcp_client_pool_creates_new() -> None:
    p1 = get_mcp_client_pool()
    reset_mcp_client_pool()
    p2 = get_mcp_client_pool()
    assert p1 is not p2


# ---------------------------------------------------------------------------
# MCPError
# ---------------------------------------------------------------------------


def test_mcp_error() -> None:
    e = MCPError(code=-32601, message="Method not found")
    assert e.code == -32601
    assert "Method not found" in str(e)


# ---------------------------------------------------------------------------
# Pool connect failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_stdio_invalid_command() -> None:
    """Connecting to a nonexistent command returns error connection."""
    pool = get_mcp_client_pool()
    conn = await pool.connect_stdio(
        "test-bad",
        ["nonexistent_command_xyz_123"],
    )
    assert not conn.connected
    assert conn.error is not None


# ---------------------------------------------------------------------------
# Pool disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_nonexistent_no_error() -> None:
    pool = get_mcp_client_pool()
    await pool.disconnect("no-such-server")


@pytest.mark.asyncio
async def test_disconnect_all_empty() -> None:
    pool = get_mcp_client_pool()
    await pool.disconnect_all()


# ---------------------------------------------------------------------------
# call_tool without connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_when_not_connected() -> None:
    pool = get_mcp_client_pool()
    result = await pool.call_tool("no-such", "test_tool")
    assert result["is_error"] is True


# ---------------------------------------------------------------------------
# list_tools without connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_when_not_connected() -> None:
    pool = get_mcp_client_pool()
    with pytest.raises(MCPError):
        await pool.list_tools("no-such")


# ---------------------------------------------------------------------------
# is_connected / get_connection
# ---------------------------------------------------------------------------


def test_is_connected_false_by_default() -> None:
    pool = get_mcp_client_pool()
    assert not pool.is_connected("nonexistent")


def test_get_connection_none_by_default() -> None:
    pool = get_mcp_client_pool()
    assert pool.get_connection("nonexistent") is None


# ---------------------------------------------------------------------------
# Echo server integration (if available)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_connect_and_call_echo_server(tmp_path: Path) -> None:
    """Integration test: connect to the alignment echo MCP server and call it."""
    pool = get_mcp_client_pool()

    import sys

    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "hare" / "alignment" / "seeds" / "mcp_echo_server.py"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,  # unbuffered
        # The server drops a call marker in its cwd (the alignment cases use it
        # as proof the tool really ran); without this it lands in the repo root.
        cwd=tmp_path,
    )

    from hare.services.mcp.client import StdioSession

    session = StdioSession(server_name="echo", process=proc)

    loop = asyncio.get_running_loop()
    session.reader_task = loop.create_task(
        pool._read_loop(session), name="mcp-reader-echo"
    )

    try:
        # Small delay for subprocess to be ready
        await asyncio.sleep(0.1)

        # Initialize
        init = await pool._send_request(
            session,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
            timeout=10,
        )
        assert "capabilities" in init

        await pool._send_notification(session, "notifications/initialized", {})

        # List tools
        tools = await pool._send_request(session, "tools/list", {}, timeout=10)
        assert len(tools["tools"]) == 1
        assert tools["tools"][0]["name"] == "echo"

        # Call tool
        result = await pool._send_request(
            session,
            "tools/call",
            {
                "name": "echo",
                "arguments": {"text": "hello"},
            },
            timeout=10,
        )
        content = result["content"]
        assert len(content) == 1
        assert "hello" in content[0]["text"]

    finally:
        await pool._close_session(session)
