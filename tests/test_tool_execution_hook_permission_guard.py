"""Regression test: a broken resolve_hook_permission_decision() must not
crash the whole tool-execution turn.

hare/services/tools/tool_execution.py wraps the PreToolUse hook-execution
call (run_pre_tool_use_hooks) in try/except so a broken hook cannot kill the
turn. The follow-up call — resolve_hook_permission_decision(...), which
internally calls can_use_tool() (and, through it, tool.check_permissions())
in several branches with no guard of its own — had no such protection. If
any of that raised, the exception propagated straight out of run_tool_use()
and crashed the turn instead of degrading gracefully like its sibling.

This test registers a PreToolUse hook that returns an 'ask' permission
decision (which routes through resolve_hook_permission_decision's
HOOK-ASK branch, calling can_use_tool unconditionally), and supplies a
can_use_tool that raises on its first call. The turn must survive: it
should fall through to the normal rule-based permission flow (as if the
hook had made no decision at all — matching the existing
`except Exception: hook_permission = None` precedent), not propagate.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hare.app_types.permissions import PermissionAllowDecision
from hare.services.tools.tool_execution import run_tool_use
from hare.tool import ToolBase, ToolResult, ToolUseContext, ToolUseContextOptions
from hare.utils.hooks import get_hook_registry


class _RecordingTool(ToolBase):
    name = "Write"
    aliases: list[str] = []

    def __init__(self) -> None:
        self.called = False

    async def call(
        self,
        args: dict[str, Any],
        context: ToolUseContext,
        can_use_tool: Any,
        parent_message: Any,
        on_progress: Any = None,
    ) -> ToolResult:
        self.called = True
        return ToolResult(data="tool ran")


class _AssistantMessage:
    uuid = "assistant-1"


def _collect(tool_use: Any, assistant_message: Any, can_use_tool: Any, context: Any) -> list[Any]:
    async def _run() -> list[Any]:
        return [
            update
            async for update in run_tool_use(
                tool_use, assistant_message, can_use_tool, context
            )
        ]

    return asyncio.run(_run())


def test_broken_hook_permission_resolution_does_not_crash_turn() -> None:
    registry = get_hook_registry()
    registry.clear()

    async def ask_hook(context: dict) -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "needs confirmation",
            }
        }

    registry.register("PreToolUse", "test-ask", ask_hook, source="test")

    tool = _RecordingTool()
    tool_use_context = ToolUseContext(options=ToolUseContextOptions(tools=[tool]))
    tool_use = {"name": "Write", "id": "tool-1", "input": {"path": "out.txt"}}

    call_count = {"n": 0}

    async def flaky_can_use_tool(
        tool_arg: Any,
        input_args: dict[str, Any],
        context: Any,
        assistant_message: Any,
        tool_use_id: str,
        force_decision: Any = None,
    ) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulates a buggy tool.check_permissions() raising inside
            # resolve_hook_permission_decision's HOOK-ASK branch.
            raise RuntimeError("boom - simulated buggy check_permissions")
        return PermissionAllowDecision(behavior="allow", updated_input=input_args)

    try:
        updates = _collect(tool_use, _AssistantMessage(), flaky_can_use_tool, tool_use_context)
    finally:
        registry.clear()

    # The broken hook-permission resolution should not have killed the turn:
    # the pipeline should have fallen through to the normal permission check
    # (a second can_use_tool call), which allowed the tool, and the tool
    # itself should have actually run.
    assert call_count["n"] == 2
    assert tool.called is True

    # No error-carrying MessageUpdateLazy should be present; the tool result
    # should reflect the tool actually running rather than an error path.
    assert updates, "expected at least one MessageUpdateLazy from the tool call"
    last = updates[-1]
    content = last.message.message.content if last.message is not None else None
    assert isinstance(content, list) and content
    assert content[0].get("is_error") is not True
