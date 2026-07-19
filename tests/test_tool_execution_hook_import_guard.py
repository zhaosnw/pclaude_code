"""Regression test: a broken tool_hooks import must not crash the turn, and
must not be silently indistinguishable from an ordinary runtime hook failure.

hare/services/tools/tool_execution.py wraps every `from
hare.services.tools.tool_hooks import ...` inside the same try/except that
guards the hook's runtime execution. A `noqa: BLE001` `except Exception`
there previously caught ImportError too — so a refactor that renamed or
removed a tool_hooks function would silently disable that hook pipeline
(same symptom as an unrelated hook throwing at runtime) with no signal
anywhere that the *wiring* itself, not a third-party hook, was broken.

Each of the four hook call sites now has a dedicated `except ImportError`
before the generic `except Exception`, which logs at ERROR level (still
falling through gracefully — a broken import must not kill the turn either).
This test breaks the import for the PreToolUse and PostToolUse call sites by
deleting the target name from the real module, and asserts: (1) the turn
still completes without raising, and (2) a distinguishing error log fires.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import hare.services.tools.tool_hooks as tool_hooks_module
from hare.services.tools.tool_execution import run_tool_use
from hare.tool import ToolBase, ToolResult, ToolUseContext, ToolUseContextOptions


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


async def _allow_can_use_tool(
    tool_arg: Any,
    input_args: dict[str, Any],
    context: Any,
    assistant_message: Any,
    tool_use_id: str,
    force_decision: Any = None,
) -> Any:
    from hare.app_types.permissions import PermissionAllowDecision

    return PermissionAllowDecision(behavior="allow", updated_input=input_args)


def _collect(tool_use: Any, assistant_message: Any, can_use_tool: Any, context: Any) -> list[Any]:
    async def _run() -> list[Any]:
        return [
            update
            async for update in run_tool_use(
                tool_use, assistant_message, can_use_tool, context
            )
        ]

    return asyncio.run(_run())


def test_broken_pre_tool_use_hook_import_logs_and_falls_through(caplog) -> None:
    tool = _RecordingTool()
    tool_use_context = ToolUseContext(options=ToolUseContextOptions(tools=[tool]))
    tool_use = {"name": "Write", "id": "tool-1", "input": {"path": "out.txt"}}

    original = tool_hooks_module.run_pre_tool_use_hooks
    del tool_hooks_module.run_pre_tool_use_hooks
    try:
        with caplog.at_level(logging.ERROR, logger="hare.services.tools.tool_execution"):
            updates = _collect(
                tool_use, _AssistantMessage(), _allow_can_use_tool, tool_use_context
            )
    finally:
        tool_hooks_module.run_pre_tool_use_hooks = original

    # Turn survived and the tool actually ran — a broken import degrades
    # gracefully exactly like a broken hook does.
    assert tool.called is True
    assert updates
    content = updates[-1].message.message.content
    assert content[0].get("is_error") is not True

    # But it must not look like an ordinary hook failure: a distinct ERROR
    # log should be present naming the import failure specifically.
    assert any(
        "PreToolUse hook import failed" in r.message for r in caplog.records
    ), [r.message for r in caplog.records]


def test_broken_post_tool_use_hook_import_logs_and_does_not_break_result(caplog) -> None:
    tool = _RecordingTool()
    tool_use_context = ToolUseContext(options=ToolUseContextOptions(tools=[tool]))
    tool_use = {"name": "Write", "id": "tool-1", "input": {"path": "out.txt"}}

    original = tool_hooks_module.run_post_tool_use_hooks
    del tool_hooks_module.run_post_tool_use_hooks
    try:
        with caplog.at_level(logging.ERROR, logger="hare.services.tools.tool_execution"):
            updates = _collect(
                tool_use, _AssistantMessage(), _allow_can_use_tool, tool_use_context
            )
    finally:
        tool_hooks_module.run_post_tool_use_hooks = original

    assert tool.called is True
    assert updates
    content = updates[-1].message.message.content
    assert content[0].get("is_error") is not True

    assert any(
        "PostToolUse" in r.message and "import failed" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]
