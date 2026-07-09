"""
Unit tests for hare.tools — tool registry, filtering, pool assembly.

Port of: src/tools.ts behavior verification.

Tests cover:
  - get_all_base_tools() returns expected tools
  - get_tools() filters by permission context deny rules
  - assemble_tool_pool() merges built-in + MCP tools
  - parse_tool_preset()
  - filter_tools_by_deny_rules()
"""

from __future__ import annotations


from hare.tool import ToolBase
from hare.tools import (
    assemble_tool_pool,
    filter_tools_by_deny_rules,
    get_all_base_tools,
    get_merged_tools,
    get_tools,
    get_tools_for_default_preset,
    parse_tool_preset,
)
from hare.app_types.permissions import (
    ToolPermissionContext,
    ToolPermissionRulesBySource,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deny_context(*tool_names: str) -> ToolPermissionContext:
    """Create a permission context that blanket-denies the given tool names."""
    deny_rules: ToolPermissionRulesBySource = {"user": list(tool_names)}
    return ToolPermissionContext(
        mode="default",
        always_deny_rules=deny_rules,
        always_allow_rules={},
        always_ask_rules={},
        additional_working_directories={},
    )


def _make_allow_context() -> ToolPermissionContext:
    return ToolPermissionContext(
        mode="default",
        always_deny_rules={},
        always_allow_rules={},
        always_ask_rules={},
        additional_working_directories={},
    )


# ---------------------------------------------------------------------------
# parse_tool_preset
# ---------------------------------------------------------------------------


def test_parse_tool_preset_valid() -> None:
    assert parse_tool_preset("default") == "default"
    assert parse_tool_preset("DEFAULT") == "default"


def test_parse_tool_preset_invalid() -> None:
    assert parse_tool_preset("unknown") is None
    assert parse_tool_preset("") is None


# ---------------------------------------------------------------------------
# get_all_base_tools
# ---------------------------------------------------------------------------


def test_get_all_base_tools_returns_non_empty_list() -> None:
    tools = get_all_base_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_get_all_base_tools_has_expected_core_tools() -> None:
    tools = get_all_base_tools()
    names = {t.name for t in tools}
    expected = {"Bash", "Read", "Edit", "Write", "Glob", "Grep", "Agent", "TodoWrite"}
    missing = expected - names
    assert not missing, f"Missing core tools: {missing}"


def test_get_all_base_tools_each_has_name() -> None:
    tools = get_all_base_tools()
    for t in tools:
        assert t.name, f"Tool has no name: {t}"


def test_all_base_tools_are_enabled_by_default() -> None:
    tools = get_all_base_tools()
    for t in tools:
        assert t.is_enabled() is True, f"Tool {t.name} not enabled by default"


def test_all_base_tools_have_input_schema() -> None:
    tools = get_all_base_tools()
    for t in tools:
        schema = t.input_schema()
        assert isinstance(schema, dict), f"{t.name}: schema is not dict"
        assert "type" in schema, f"{t.name}: schema missing 'type'"


# ---------------------------------------------------------------------------
# get_tools_for_default_preset
# ---------------------------------------------------------------------------


def test_get_tools_for_default_preset() -> None:
    tool_names = get_tools_for_default_preset()
    assert isinstance(tool_names, list)
    assert len(tool_names) > 0
    assert "Bash" in tool_names


# ---------------------------------------------------------------------------
# get_tools
# ---------------------------------------------------------------------------


def test_get_tools_with_no_deny_rules() -> None:
    tools = get_tools(_make_allow_context())
    assert len(tools) > 0
    names = {t.name for t in tools}
    assert "Bash" in names


def test_get_tools_filters_denied_tool() -> None:
    ctx = _make_deny_context("Bash")
    tools = get_tools(ctx)
    names = {t.name for t in tools}
    assert "Bash" not in names, "Bash should be denied"


def test_get_tools_filters_multiple_denied() -> None:
    ctx = _make_deny_context("Bash", "Read")
    tools = get_tools(ctx)
    names = {t.name for t in tools}
    assert "Bash" not in names
    assert "Read" not in names


# ---------------------------------------------------------------------------
# filter_tools_by_deny_rules
# ---------------------------------------------------------------------------


def test_filter_tools_by_deny_rules_no_rules() -> None:
    tools = get_all_base_tools()
    filtered = filter_tools_by_deny_rules(tools, _make_allow_context())
    assert len(filtered) == len(tools)


def test_filter_tools_by_deny_rules_removes_denied() -> None:
    tools = get_all_base_tools()
    ctx = _make_deny_context("Bash")
    filtered = filter_tools_by_deny_rules(tools, ctx)
    assert len(filtered) < len(tools)
    names = {t.name for t in filtered}
    assert "Bash" not in names


# ---------------------------------------------------------------------------
# assemble_tool_pool
# ---------------------------------------------------------------------------


def test_assemble_tool_pool_no_mcp() -> None:
    tools = assemble_tool_pool(_make_allow_context())
    assert len(tools) > 0
    # Should be sorted by name
    names = [t.name for t in tools]
    assert names == sorted(names), "Tool pool should be sorted by name"


def test_assemble_tool_pool_dedup_mcp() -> None:
    class _McpTool(ToolBase):
        name = "Bash"
        aliases = []

    mcp_tools = [_McpTool()]
    tools = assemble_tool_pool(_make_allow_context(), mcp_tools=mcp_tools)
    # MCP tool with same name as built-in should be deduped
    bash_count = sum(1 for t in tools if t.name == "Bash")
    assert bash_count == 1, "Duplicate Bash tools should be deduped"


def test_assemble_tool_pool_new_mcp_tool_preserved() -> None:
    class _NovelMcpTool(ToolBase):
        name = "novel_mcp_tool"
        aliases = []

    mcp_tools = [_NovelMcpTool()]
    tools = assemble_tool_pool(_make_allow_context(), mcp_tools=mcp_tools)
    names = {t.name for t in tools}
    assert "novel_mcp_tool" in names


def test_assemble_tool_pool_applies_deny_to_mcp() -> None:
    class _DeniedMcpTool(ToolBase):
        name = "denied_mcp"
        aliases = []

    mcp_tools = [_DeniedMcpTool()]
    ctx = _make_deny_context("denied_mcp")
    tools = assemble_tool_pool(ctx, mcp_tools=mcp_tools)
    names = {t.name for t in tools}
    assert "denied_mcp" not in names


# ---------------------------------------------------------------------------
# get_merged_tools
# ---------------------------------------------------------------------------


def test_get_merged_tools_no_mcp() -> None:
    tools = get_merged_tools(_make_allow_context())
    base = get_tools(_make_allow_context())
    assert len(tools) == len(base)


def test_get_merged_tools_with_mcp() -> None:
    class _McpTool(ToolBase):
        name = "mcp_extra"
        aliases = []

    mcp = [_McpTool()]
    tools = get_merged_tools(_make_allow_context(), mcp_tools=mcp)
    names = {t.name for t in tools}
    assert "mcp_extra" in names
    assert "Bash" in names


# ---------------------------------------------------------------------------
# Tool alias matching in deny rules
# ---------------------------------------------------------------------------


def test_deny_rule_matches_by_alias() -> None:
    class _AliasedTool(ToolBase):
        name = "alias_primary"
        aliases = ["ap", "alias_alt"]

    ctx = _make_deny_context("alias_alt")
    tools = [_AliasedTool()]
    filtered = filter_tools_by_deny_rules(tools, ctx)
    assert len(filtered) == 0
