"""
/review command - submit changes for AI code review.

Port of: src/commands/review.ts + review/ (4 files)

Submits the current diff or specified changes for AI review.
In the TS CLI this triggers a remote review flow.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "review"
DESCRIPTION = "Review recent changes with AI analysis"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Submit changes for AI review."""
    get_app_state = context.get("get_app_state")
    is_ultrareview_enabled = context.get("is_ultrareview_enabled")

    # Check if ultrareview is available
    if is_ultrareview_enabled and is_ultrareview_enabled():
        return {
            "type": "text",
            "value": (
                "## Code Review\n\n"
                "Use `/review` to submit the current changes for analysis.\n"
                "The review will check for:\n"
                "- Security issues\n"
                "- Code quality concerns\n"
                "- Potential bugs\n"
                "- Best practice violations\n\n"
                "Run `/diff` first to see current changes, then `/review` to analyze them."
            ),
        }

    return {
        "type": "text",
        "value": (
            "## Code Review\n\n"
            "Submit changes for AI review. Use with `/diff` to see current changes.\n\n"
            "The AI will analyze your diff and provide feedback on:\n"
            "- Security issues\n"
            "- Code quality\n"
            "- Potential bugs\n"
            "- Best practices\n\n"
            "Include specific instructions: `/review focus on error handling`"
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[focus area]",
        "call": call,
    }
