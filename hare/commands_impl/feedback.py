"""
/feedback command - send feedback about the CLI/SDK.

Port of: src/commands/feedback/feedback.tsx + index.ts

Captures user feedback with optional category and submits to analytics.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "feedback"
DESCRIPTION = "Send feedback about Hare"
ALIASES = ["bug"]

FEEDBACK_CATEGORIES = ["bug", "feature", "improvement", "other"]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Capture and log user feedback."""
    text = (args or "").strip()
    if not text:
        return {
            "type": "text",
            "value": (
                "Usage: /feedback <your feedback>\n\n"
                "Please describe your feedback, bug report, or feature request.\n"
                f"Optional categories: {', '.join(FEEDBACK_CATEGORIES)}"
            ),
            "display": "system",
        }

    # Parse category from first word if it matches
    category = "general"
    parts = text.split(None, 1)
    if parts[0].lower() in FEEDBACK_CATEGORIES:
        category = parts[0].lower()
        text = parts[1] if len(parts) > 1 else ""

    # Log the feedback event
    log_event = context.get("log_event")
    if log_event:
        log_event(
            "tengu_user_feedback",
            {
                "category": category,
                "feedback_text": text[:500],
                "source": "slash_command",
            },
        )

    # Submit to feedback API if available
    submit_feedback = context.get("submit_feedback")
    if submit_feedback:
        try:
            await submit_feedback({"category": category, "text": text})
        except Exception:
            pass

    return {
        "type": "text",
        "value": (
            f"Thank you for your feedback! ({category})\n\n"
            "Your input helps us improve. For urgent issues, "
            "please visit https://github.com/anthropics/claude-code/issues"
        ),
    }


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argumentHint": "[bug|feature|improvement|other] <feedback>",
        "call": call,
    }
