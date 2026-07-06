"""
EnterPlanModeTool — switch to plan mode where tools are restricted.

Port of: src/tools/EnterPlanModeTool/EnterPlanModeTool.ts

Plan mode restricts the model to read-only operations for designing
implementation strategies before making any changes.
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "EnterPlanMode"
DESCRIPTION = (
    "Enter plan mode — use this before implementing non-trivial changes. "
    "In plan mode you can explore the codebase and design an approach, "
    "but cannot make file edits or run commands that modify the system."
)


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "Brief description of what you're planning to implement",
            },
        },
    }


async def call(plan: str = "", **kwargs: Any) -> dict[str, Any]:
    """Switch to plan mode."""
    return {
        "mode": "plan",
        "plan_topic": plan,
        "data": (
            "Entered plan mode. Your tools are now restricted to read-only operations. "
            "Explore the codebase thoroughly, then use ExitPlanMode when you have a "
            "complete implementation plan ready for approval."
        ),
        "allowed_tools": [
            "FileRead", "Glob", "Grep", "Bash", "WebFetch", "WebSearch",
            "Task", "TodoWrite", "AskUserQuestion",
        ],
    }
