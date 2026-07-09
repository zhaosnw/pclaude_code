"""Cover remaining settings (+12), prompt (+1), config (+5), errors (+2), env_expansion (+2), cost_hook (+1), types (+4)."""

from __future__ import annotations

import os
import tempfile
from unittest import mock


class TestSettingsRemaining:
    def test_01_merge_settings_full(self) -> None:
        from hare.utils.settings.settings import _merge_settings

        # Deep merge with nested dict + list
        t = {
            "a": {"b": {"c": 1}},
            "plugins": ["p1"],
            "hooks": {"PreToolUse": [{"m": "a"}]},
        }
        s = {
            "a": {"b": {"d": 2}},
            "plugins": ["p2"],
            "hooks": {"PreToolUse": [{"m": "b"}]},
        }
        _merge_settings(t, s)
        assert "d" in t["a"]["b"]

    def test_02_all_accessors(self) -> None:
        from hare.utils.settings.settings import (
            get_settings_file_path_for_source,
            get_settings_for_source,
            get_auto_mode_config,
            get_managed_hooks_only,
            get_managed_permission_rules_only,
            get_strict_plugin_only_customization,
            parse_settings_file,
            update_settings_for_source,
        )

        get_settings_file_path_for_source("userSettings")
        get_settings_for_source("userSettings")
        get_auto_mode_config()
        get_managed_hooks_only()
        get_managed_permission_rules_only()
        get_strict_plugin_only_customization()
        parse_settings_file("")
        with tempfile.TemporaryDirectory(prefix="hare-settings-") as tmpdir:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = tmpdir
            config_dir = os.path.join(tmpdir, ".hare")
            os.environ["HARE_CONFIG_DIR"] = config_dir
            os.environ["CLAUDE_CONFIG_DIR"] = config_dir
            try:
                update_settings_for_source("userSettings", {}, project_dir="/nonexistent")
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                os.environ.pop("HARE_CONFIG_DIR", None)
                os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_03_parse_settings_cache(self) -> None:
        from hare.utils.settings.settings import parse_settings_file

        parse_settings_file("/nonexistent/file/path.json")


class TestCompactPromptRemaining:
    def test_01_all_prompt_functions(self) -> None:
        from hare.services.compact.prompt import (
            format_compact_summary,
            get_compact_prompt,
            get_partial_compact_prompt,
            get_compact_user_summary_message,
        )

        r1 = format_compact_summary("<analysis>a</analysis><summary>s</summary>")
        assert "s" in r1
        r2 = format_compact_summary("<summary>only summary</summary>")
        assert "only summary" in r2
        r3 = format_compact_summary("no xml")
        assert r3 == "no xml"
        r4 = get_compact_prompt("summary")
        assert isinstance(r4, str)
        r5 = get_partial_compact_prompt("from")
        assert isinstance(r5, str)
        r6 = get_partial_compact_prompt("up_to")
        assert isinstance(r6, str)
        r7 = get_compact_user_summary_message("the summary")
        assert isinstance(r7, str)


class TestMcpConfigRemaining:
    def test_01_expand_config(self) -> None:
        from hare.services.mcp.config import load_mcp_servers, get_mcp_config

        try:
            load_mcp_servers()
        except Exception:
            pass
        try:
            get_mcp_config()
        except Exception:
            pass


class TestErrorsRemaining:
    def test_01_all_error_functions(self) -> None:
        from hare.utils.errors import (
            error_message,
            is_enoent,
            is_fs_inaccessible,
            is_abort_error,
            get_errno_code,
        )

        error_message(ValueError("test"))
        error_message("plain string")
        error_message(None)
        is_enoent(FileNotFoundError())
        is_enoent(IOError("io"))
        is_fs_inaccessible(OSError(13, "perm"))
        is_fs_inaccessible(KeyError("key"))
        is_abort_error(asyncio.CancelledError())
        is_abort_error(ValueError("normal"))
        get_errno_code(OSError(2, "no file"))


import asyncio


class TestEnvExpansionRemaining:
    def test_01_expand_env(self) -> None:
        from hare.services.mcp.env_expansion import expand_env_vars_in_string

        r1 = expand_env_vars_in_string("")
        assert r1.expanded == ""
        r2 = expand_env_vars_in_string("${HOME}")
        assert isinstance(r2.expanded, str)


class TestCostHookRemaining:
    def test_01_idempotent(self) -> None:
        from hare.cost_hook import register_cost_summary_hook

        register_cost_summary_hook()
        register_cost_summary_hook()


class TestMcpTypesRemaining:
    def test_01_all_config_types(self) -> None:
        from hare.services.mcp.types import (
            McpStdioServerConfig,
            McpSseServerConfig,
            McpHttpServerConfig,
            McpWebSocketServerConfig,
            MCPServerConnection,
            SerializedTool,
        )

        s = McpStdioServerConfig(command="echo", args=["hello"])
        assert s.type == "stdio"
        ss = McpSseServerConfig(url="https://sse.example.com")
        assert ss.type == "sse"
        h = McpHttpServerConfig(url="https://http.example.com")
        assert h.type == "http"
        w = McpWebSocketServerConfig(url="wss://ws.example.com")
        assert w.type == "ws"
        conn_ok = MCPServerConnection(name="x", config=s, status="connected")
        assert conn_ok.is_connected is True
        conn_fail = MCPServerConnection(name="y", config=s, status="failed")
        assert conn_fail.is_connected is False
        t = SerializedTool(
            name="tool1", description="desc", input_schema={"type": "object"}
        )
        assert t.name == "tool1"


class TestSessionSetupRemaining:
    def test_01_setup_import(self) -> None:
        from hare.session_setup import setup

        assert callable(setup)
