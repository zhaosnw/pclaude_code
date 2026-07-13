from __future__ import annotations

import asyncio

from hare.app_types.permissions import PermissionDenyDecision
from hare.query.query_test_helpers import make_tool_use_context
from hare.tool import ToolBase
from hare.utils.permissions.permissions import has_permissions_to_use_tool
from hare.utils.permissions.permissions_loader import load_permission_context


class _WriteTool(ToolBase):
    name = "Write"
    aliases: list[str] = []

    def is_read_only(self, input: dict) -> bool:
        return False


def test_load_permission_context_merges_settings_and_cli_rules(tmp_path, monkeypatch):
    import hare.utils.permissions.permissions_loader as loader

    home = tmp_path / "home"
    monkeypatch.setattr(loader, "_HARE_HOME", home)
    (tmp_path / ".hare").mkdir()
    (tmp_path / ".hare" / "settings.json").write_text(
        '{"permissions": {"allow": ["Read"], "deny": ["Bash(rm *)"], "ask": ["Write"]}}',
        encoding="utf-8",
    )

    context = load_permission_context(
        tmp_path,
        mode="bypassPermissions",
        allowed_tools=["Edit"],
        disallowed_tools=["mcp__echo"],
    )

    assert context.mode == "bypassPermissions"
    assert context.is_bypass_permissions_mode_available is True
    assert context.always_allow_rules["projectSettings"] == ["Read"]
    assert context.always_ask_rules["projectSettings"] == ["Write"]
    assert context.always_deny_rules["projectSettings"] == ["Bash(rm *)"]
    assert context.always_allow_rules["cliArg"] == ["Edit"]
    assert context.always_deny_rules["cliArg"] == ["mcp__echo"]


def test_permission_context_is_used_by_query_tool_checks():
    tool = _WriteTool()
    base_context = make_tool_use_context(tools=[tool])
    permission_context = load_permission_context(mode="default", disallowed_tools=["Write"])
    base_context.options.permission_context = permission_context

    result = asyncio.run(
        has_permissions_to_use_tool(
            tool,
            {"path": "out.txt"},
            base_context,
            None,
            "tool-use-1",
        )
    )

    assert isinstance(result, PermissionDenyDecision)
    assert result.behavior == "deny"


def test_bypass_mode_allows_write_but_deny_rule_still_wins():
    tool = _WriteTool()
    context = make_tool_use_context(tools=[tool])
    context.options.permission_context = load_permission_context(
        mode="bypassPermissions", disallowed_tools=["Write"]
    )

    result = asyncio.run(
        has_permissions_to_use_tool(tool, {"path": "out.txt"}, context, None, "tool-use-2")
    )

    assert isinstance(result, PermissionDenyDecision)
