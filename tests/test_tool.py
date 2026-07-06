"""
Unit tests for hare.tool — Tool Protocol, ToolBase, helpers.

Port of: src/Tool.ts behavior verification.
"""

from __future__ import annotations

import pytest

from hare.tool import (
    ToolBase,
    ToolResult,
    ToolUseContext,
    ToolUseContextOptions,
    ValidationResultError,
    ValidationResultOK,
    filter_tool_progress_messages,
    find_tool_by_name,
    get_empty_tool_permission_context,
    tool_matches_name,
)
from hare.app_types.message import ProgressMessage
from hare.app_types.permissions import PermissionAllowDecision


# ---------------------------------------------------------------------------
# ToolBase defaults
# ---------------------------------------------------------------------------


class _MinimalTool(ToolBase):
    name = "minimal"


class _ReadOnlyTool(ToolBase):
    name = "reader"

    def is_read_only(self, input: dict) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class _DestructiveTool(ToolBase):
    name = "destroyer"

    def is_destructive(self, input: dict) -> bool:
        return True


class _ConcurrentTool(ToolBase):
    name = "concurrent"

    def is_concurrency_safe(self, input: dict) -> bool:
        return True


class _ValidatingTool(ToolBase):
    name = "validator"

    def validate_input(self, input: dict) -> ValidationResultError | ValidationResultOK:
        if not input.get("required_field"):
            return ValidationResultError(message="required_field missing", error_code=1)
        return ValidationResultOK()

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"required_field": {"type": "string"}},
            "required": ["required_field"],
        }


class _SchemaTool(ToolBase):
    name = "schema_tool"

    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"x": {"type": "integer"}}}

    def output_schema(self) -> dict | None:
        return {"type": "object", "properties": {"result": {"type": "string"}}}


class _EquivalentTool(ToolBase):
    name = "equivalent"

    def inputs_equivalent(self, a: dict, b: dict) -> bool:
        return a.get("key") == b.get("key")


# ---------------------------------------------------------------------------
# ToolBase default behavior
# ---------------------------------------------------------------------------


def test_tool_base_defaults() -> None:
    t = _MinimalTool()
    assert t.name == "minimal"
    assert t.is_enabled() is True
    assert t.is_read_only({}) is False
    assert t.is_destructive({}) is False
    assert t.is_concurrency_safe({}) is False
    assert t.user_facing_name() == "minimal"
    assert t.to_auto_classifier_input({}) == ""
    assert t.max_result_size_chars == 100_000


def test_tool_base_input_schema_default() -> None:
    t = _MinimalTool()
    schema = t.input_schema()
    assert schema == {"type": "object", "properties": {}}


def test_tool_base_validate_input_default() -> None:
    t = _MinimalTool()
    result = t.validate_input({})
    assert isinstance(result, ValidationResultOK)
    assert result.result is True


@pytest.mark.asyncio
async def test_tool_base_check_permissions_default() -> None:
    t = _MinimalTool()
    ctx = ToolUseContext()
    result = await t.check_permissions({}, ctx)
    assert isinstance(result, PermissionAllowDecision)
    assert result.behavior == "allow"


def test_tool_base_map_tool_result_default() -> None:
    t = _MinimalTool()
    result = t.map_tool_result_to_tool_result_block_param("hello", "id123")
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "id123"
    assert result["content"] == "hello"


# ---------------------------------------------------------------------------
# Read-only / destructive / concurrency
# ---------------------------------------------------------------------------


def test_read_only_tool() -> None:
    t = _ReadOnlyTool()
    assert t.is_read_only({}) is True
    assert t.is_destructive({}) is False
    assert t.is_concurrency_safe({}) is False


def test_destructive_tool() -> None:
    t = _DestructiveTool()
    assert t.is_destructive({}) is True
    assert t.is_read_only({}) is False


def test_concurrent_tool() -> None:
    t = _ConcurrentTool()
    assert t.is_concurrency_safe({}) is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validation_ok() -> None:
    v = ValidationResultOK()
    assert v.result is True


def test_validation_error() -> None:
    v = ValidationResultError(message="bad", error_code=42)
    assert v.result is False
    assert v.message == "bad"
    assert v.error_code == 42


def test_validating_tool_rejects_missing_required() -> None:
    t = _ValidatingTool()
    result = t.validate_input({})
    assert isinstance(result, ValidationResultError)
    assert result.result is False
    assert result.message == "required_field missing"


def test_validating_tool_accepts_valid_input() -> None:
    t = _ValidatingTool()
    result = t.validate_input({"required_field": "present"})
    assert isinstance(result, ValidationResultOK)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_input_schema_custom() -> None:
    t = _SchemaTool()
    schema = t.input_schema()
    assert schema["properties"]["x"]["type"] == "integer"


def test_output_schema_custom() -> None:
    t = _SchemaTool()
    schema = t.output_schema()
    assert schema is not None
    assert "result" in schema.get("properties", {})


def test_output_schema_default_none() -> None:
    t = _MinimalTool()
    assert t.output_schema() is None


# ---------------------------------------------------------------------------
# Inputs equivalent
# ---------------------------------------------------------------------------


def test_inputs_equivalent_default_false() -> None:
    t = _MinimalTool()
    assert t.inputs_equivalent({}, {}) is False


def test_inputs_equivalent_custom() -> None:
    t = _EquivalentTool()
    assert t.inputs_equivalent({"key": "a"}, {"key": "a"}) is True
    assert t.inputs_equivalent({"key": "a"}, {"key": "b"}) is False


# ---------------------------------------------------------------------------
# tool_matches_name
# ---------------------------------------------------------------------------


def test_tool_matches_name_exact() -> None:
    t = _MinimalTool()
    assert tool_matches_name(t, "minimal") is True
    assert tool_matches_name(t, "other") is False


def test_tool_matches_name_alias() -> None:
    t = _MinimalTool()
    t.aliases = ["min", "m"]
    assert tool_matches_name(t, "min") is True
    assert tool_matches_name(t, "m") is True
    assert tool_matches_name(t, "other") is False


# ---------------------------------------------------------------------------
# find_tool_by_name
# ---------------------------------------------------------------------------


def test_find_tool_by_name_found() -> None:
    a = _MinimalTool()
    a.name = "aaa"
    b = _ReadOnlyTool()
    b.name = "bbb"
    result = find_tool_by_name([a, b], "bbb")
    assert result is b


def test_find_tool_by_name_alias() -> None:
    a = _MinimalTool()
    a.name = "aaa"
    a.aliases = ["al"]
    result = find_tool_by_name([a], "al")
    assert result is a


def test_find_tool_by_name_not_found() -> None:
    result = find_tool_by_name([], "nope")
    assert result is None


# ---------------------------------------------------------------------------
# get_empty_tool_permission_context
# ---------------------------------------------------------------------------


def test_empty_permission_context() -> None:
    ctx = get_empty_tool_permission_context()
    assert ctx.mode == "default"
    assert ctx.always_allow_rules == {}
    assert ctx.always_deny_rules == {}
    assert ctx.always_ask_rules == {}
    assert ctx.is_bypass_permissions_mode_available is False


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


def test_tool_result_defaults() -> None:
    r = ToolResult()
    assert r.data is None
    assert r.new_messages == []
    assert r.context_modifier is None
    assert r.mcp_meta is None


def test_tool_result_with_data() -> None:
    r = ToolResult(data={"key": "value"})
    assert r.data == {"key": "value"}


def test_tool_result_with_context_modifier() -> None:
    called = []

    def modifier(ctx):
        called.append(1)
        return ctx

    r = ToolResult(context_modifier=modifier)
    ctx = ToolUseContext()
    result = r.context_modifier(ctx)
    assert result is ctx
    assert len(called) == 1


# ---------------------------------------------------------------------------
# ToolUseContext
# ---------------------------------------------------------------------------


def test_tool_use_context_defaults() -> None:
    ctx = ToolUseContext()
    assert isinstance(ctx.options, ToolUseContextOptions)
    assert ctx.abort_controller is None
    assert ctx.read_file_state == {}
    assert ctx.messages == []
    assert ctx.file_reading_limits == {}
    assert ctx.glob_limits == {}
    assert ctx.tool_decisions == {}


def test_tool_use_context_options_defaults() -> None:
    opts = ToolUseContextOptions()
    assert opts.commands == []
    assert opts.debug is False
    assert opts.tools == []
    assert opts.verbose is False
    assert opts.theme == "default"


# ---------------------------------------------------------------------------
# filter_tool_progress_messages
# ---------------------------------------------------------------------------


def test_filter_tool_progress_removes_hook_progress() -> None:
    msgs = [
        ProgressMessage(data={"type": "hook_progress"}),
        ProgressMessage(data={"type": "tool_progress"}),
    ]
    filtered = filter_tool_progress_messages(msgs)
    assert len(filtered) == 1
    assert filtered[0].data.get("type") == "tool_progress"


def test_filter_tool_progress_handles_none_data() -> None:
    msgs = [
        ProgressMessage(data=None),
        ProgressMessage(data={"type": "hook_progress"}),
    ]
    filtered = filter_tool_progress_messages(msgs)
    assert len(filtered) == 1
    assert filtered[0].data is None
