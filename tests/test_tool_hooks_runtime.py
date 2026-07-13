from __future__ import annotations

import asyncio

from hare.tool import ToolBase, ToolUseContext
from hare.utils.hooks import get_hook_registry
from hare.services.tools.tool_hooks import run_pre_tool_use_hooks


class _HookTool(ToolBase):
    name = "Write"
    aliases: list[str] = []


def test_registered_pretool_callback_blocks_tool_use():
    registry = get_hook_registry()
    registry.clear()

    async def block(context: dict) -> dict:
        assert context["tool_name"] == "Write"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "blocked by test hook",
            }
        }

    registry.register("PreToolUse", "test-block", block, source="test")
    try:
        results = asyncio.run(
            _collect_pretool_results(_HookTool(), ToolUseContext())
        )
    finally:
        registry.clear()

    decisions = [item["hookPermissionResult"] for item in results if "hookPermissionResult" in item]
    assert decisions
    assert decisions[0]["behavior"] == "deny"
    assert decisions[0]["message"] == "blocked by test hook"


async def _collect_pretool_results(tool: object, context: ToolUseContext) -> list[dict]:
    return [
        result
        async for result in run_pre_tool_use_hooks(
            context, tool, {"path": "out.txt"}, "tool-1", "message-1"
        )
    ]
