"""Unit and integration tests for MCP configuration loading."""

from __future__ import annotations

import json

import pytest

from hare.services.mcp.config import (
    _parse_server_config,
    load_mcp_servers,
    validate_mcp_config_file,
)
from hare.services.mcp.types import (
    McpHttpServerConfig,
    McpSseServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
)


# ---------------------------------------------------------------------------
# _parse_server_config
# ---------------------------------------------------------------------------


def test_parse_non_dict_returns_none() -> None:
    assert _parse_server_config("not a dict") is None
    assert _parse_server_config(None) is None
    assert _parse_server_config([]) is None


def test_parse_stdio_config() -> None:
    cfg = _parse_server_config(
        {
            "type": "stdio",
            "command": "python",
            "args": ["-c", "print(1)"],
            "env": {"FOO": "bar"},
        }
    )
    assert isinstance(cfg, McpStdioServerConfig)
    assert cfg.command == "python"
    assert cfg.args == ["-c", "print(1)"]
    assert cfg.env["FOO"] == "bar"


def test_parse_stdio_missing_command_returns_none() -> None:
    assert _parse_server_config({"type": "stdio"}) is None


def test_validate_explicit_mcp_config_rejects_non_string_stdio_command(tmp_path) -> None:
    config_path = tmp_path / "invalid-mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"broken": {"command": 42}}}),
        encoding="utf-8",
    )

    assert validate_mcp_config_file(str(config_path)) == [
        "mcpServers.broken: Does not adhere to MCP server configuration schema"
    ]


def test_parse_sse_config() -> None:
    cfg = _parse_server_config(
        {
            "type": "sse",
            "url": "http://localhost:8080/sse",
            "headers": {"Authorization": "Bearer token"},
        }
    )
    assert isinstance(cfg, McpSseServerConfig)
    assert cfg.url == "http://localhost:8080/sse"


def test_parse_http_config() -> None:
    cfg = _parse_server_config(
        {
            "type": "http",
            "url": "http://localhost:8080/mcp",
        }
    )
    assert isinstance(cfg, McpHttpServerConfig)


def test_parse_streamable_http_config() -> None:
    cfg = _parse_server_config(
        {
            "type": "streamable-http",
            "url": "http://localhost:8080/mcp",
        }
    )
    assert isinstance(cfg, McpHttpServerConfig)


def test_parse_ws_config() -> None:
    cfg = _parse_server_config(
        {
            "type": "ws",
            "url": "ws://localhost:8080/mcp",
        }
    )
    assert isinstance(cfg, McpWebSocketServerConfig)


def test_parse_defaults_to_stdio() -> None:
    cfg = _parse_server_config({"command": "echo"})
    assert isinstance(cfg, McpStdioServerConfig)


# ---------------------------------------------------------------------------
# Env var expansion
# ---------------------------------------------------------------------------


def test_stdio_env_var_expansion(monkeypatch) -> None:
    monkeypatch.setenv("MY_HOME", "/home/user")
    cfg = _parse_server_config(
        {
            "type": "stdio",
            "command": "${MY_HOME}/bin/server",
            "args": ["--config", "${MY_HOME}/config.yml"],
        }
    )
    assert isinstance(cfg, McpStdioServerConfig)
    assert cfg.command == "/home/user/bin/server"
    assert cfg.args[1] == "/home/user/config.yml"


def test_sse_url_expansion(monkeypatch) -> None:
    monkeypatch.setenv("BASE_URL", "http://api.example.com")
    cfg = _parse_server_config(
        {
            "type": "sse",
            "url": "${BASE_URL}/mcp/sse",
        }
    )
    assert isinstance(cfg, McpSseServerConfig)
    assert cfg.url == "http://api.example.com/mcp/sse"


# ---------------------------------------------------------------------------
# load_mcp_servers — settings.json
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_load_mcp_servers_from_settings_json(tmp_path) -> None:
    """Load MCP servers from a ~/.hare/settings.json file."""
    hare_dir = tmp_path / ".hare"
    hare_dir.mkdir()
    settings = hare_dir / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "my-server": {
                        "type": "stdio",
                        "command": "python",
                        "args": ["-m", "my_mcp"],
                    }
                }
            }
        )
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("HOME", str(tmp_path))

    servers = load_mcp_servers(settings_dir=str(hare_dir))
    by_name = {s.name: s for s in servers}
    # The user's settings.json server is loaded with the right config type.
    assert "my-server" in by_name
    assert isinstance(by_name["my-server"].config, McpStdioServerConfig)
    # Built-in hare_ai servers are always present (lowest precedence) — assert
    # the user server loads *on top of* them rather than a brittle total count.
    from hare.services.mcp.config import _BUILTIN_HARE_SERVERS

    assert set(_BUILTIN_HARE_SERVERS).issubset(by_name)


# ---------------------------------------------------------------------------
# load_mcp_servers — .mcp.json chain
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_load_mcp_servers_from_mcp_json(tmp_path) -> None:
    """Load MCP servers from a .mcp.json in project dir."""
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "project-server": {
                        "type": "sse",
                        "url": "http://localhost:3000/sse",
                    }
                }
            }
        )
    )

    servers = load_mcp_servers(project_dir=str(tmp_path))
    names = {s.name for s in servers}
    assert "project-server" in names
    # verify it's the right one
    proj = next(s for s in servers if s.name == "project-server")
    assert isinstance(proj.config, McpSseServerConfig)
    assert proj.scope == "project"


@pytest.mark.integration
def test_mcp_json_chain_walks_parents(tmp_path) -> None:
    """MCP config walks from child dir up to parent with .mcp.json."""
    parent_mcp = tmp_path / ".mcp.json"
    parent_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "root-server": {"type": "stdio", "command": "server-root"},
                }
            }
        )
    )

    child_dir = tmp_path / "sub" / "deep"
    child_dir.mkdir(parents=True)

    servers = load_mcp_servers(project_dir=str(child_dir))
    assert len(servers) >= 1
    names = {s.name for s in servers}
    assert "root-server" in names
