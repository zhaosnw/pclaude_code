"""Port of: src/commands/onboarding/. Interactive onboarding tutorial for new users."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "onboarding"
DESCRIPTION = "Show onboarding tutorial and getting-started guide"
ALIASES: list[str] = []


_STEPS: list[dict[str, str]] = [
    {
        "title": "Welcome to Hare",
        "body": (
            "Hare is a fast, headless AI coding assistant for your terminal. "
            "It understands your codebase and helps you write, review, and refactor code."
        ),
    },
    {
        "title": "Slash commands",
        "body": (
            "Type / to see available commands:\n"
            "  /help       — full command list\n"
            "  /clear      — reset conversation\n"
            "  /compact    — compress context to save tokens\n"
            "  /diff       — show git diff of changes\n"
            "  /commit     — generate and apply a commit\n"
            "  /review     — review your current changes"
        ),
    },
    {
        "title": "Keyboard shortcuts",
        "body": (
            "  Ctrl+C       — interrupt the assistant\n"
            "  Ctrl+D       — exit the session\n"
            "  Ctrl+R       — search command history\n"
            "  Ctrl+L       — clear the screen\n"
            "  Up/Down      — navigate message history\n"
            "  Esc Esc      — force-stop generation"
        ),
    },
    {
        "title": "Talk naturally",
        "body": (
            "You don't need slash commands for everything. Just describe what you need: "
            "'Fix the bug in auth.py', 'Explain how the router works', "
            "'Add tests for the payment module'."
        ),
    },
    {
        "title": "Context is automatic",
        "body": (
            "Hare reads relevant files from your project automatically. "
            "Use @filename to reference a specific file, or drag-and-drop files into "
            "the terminal if supported."
        ),
    },
    {
        "title": "Next steps",
        "body": (
            "/help — full reference  |  /version — version info\n"
            "/doctor — system check  |  /session — session details\n"
            "/discover — feature explorer  |  /release-notes — what's new"
        ),
    },
]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Display the onboarding tutorial, or /onboarding <N> for a single step."""
    lines: list[str] = []

    step_num = None
    for a in args:
        stripped = a.strip()
        if stripped.isdigit():
            step_num = int(stripped)
            break

    if step_num is not None and 1 <= step_num <= len(_STEPS):
        s = _STEPS[step_num - 1]
        lines.append(f"Step {step_num}/{len(_STEPS)}: {s['title']}")
        lines.append("─" * 50)
        lines.append(s["body"])
    else:
        lines.append("Getting Started with Hare")
        lines.append("=" * 50)
        for i, s in enumerate(_STEPS, 1):
            lines.append(f"  {i}. {s['title']}")
            for line in s["body"].split("\n"):
                lines.append(f"     {line}")
            lines.append("")
        lines.append("─" * 50)
        lines.append("Run /onboarding <N> (e.g. /onboarding 3) to zoom into one step.")

    return {"type": "text", "value": "\n".join(lines)}
