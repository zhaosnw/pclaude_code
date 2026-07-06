"""
/ultrareview command - extended remote review with web-based interface.

Port of: src/commands/review/ultrareviewCommand.tsx + UltrareviewOverageDialog.tsx

Provides an extended bug-hunt/review path using Claude Code on the web.
In the TS CLI this is a local-jsx command with web integration.
In the headless SDK, it provides a guide to the web-based ultrareview flow.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "ultrareview"
DESCRIPTION = "Extended remote review on Claude Code for the web"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show ultrareview status or guide."""
    is_ultrareview_enabled = context.get("is_ultrareview_enabled")
    get_ultrareview_url = context.get("get_ultrareview_url")

    if is_ultrareview_enabled and is_ultrareview_enabled():
        if get_ultrareview_url:
            url = await get_ultrareview_url()
            return {
                "type": "text",
                "value": (
                    "## Ultrareview\n\n"
                    "Ultrareview is available for deep code analysis.\n\n"
                    f"Open this URL to start an ultrareview:\n\n  {url}\n\n"
                    "Ultrareview provides:\n"
                    "- Deep multi-file analysis\n"
                    "- Cross-repository review\n"
                    "- Interactive exploration\n"
                    "- Detailed findings report"
                ),
            }

        return {
            "type": "text",
            "value": (
                "## Ultrareview\n\n"
                "Ultrareview is available.\n\n"
                "Run `/review` first to see current changes, then `/ultrareview` for deep analysis.\n\n"
                "To start an ultrareview with a specific focus:\n"
                "  `/ultrareview focus on security and performance`"
            ),
        }

    return {
        "type": "text",
        "value": (
            "## Ultrareview\n\n"
            "Ultrareview is the extended remote review feature available in Claude Code.\n\n"
            "To use it:\n"
            "1. Start Claude Code with `claude --remote`\n"
            "2. Use `/review` or `/ultrareview` in the web interface\n\n"
            "In headless mode, use `/review` for standard code review and `/security-review` for security analysis."
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
