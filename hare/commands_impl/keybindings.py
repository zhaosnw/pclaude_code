"""
/keybindings command - show keyboard shortcuts.

Port of: src/commands/keybindings/ (2 files)

Shows configured keybindings with their shortcuts and descriptions.
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "keybindings"
DESCRIPTION = "Show keyboard shortcuts"
ALIASES = ["keys"]

DEFAULT_KEYBINDINGS = [
    ("Ctrl+C", "Cancel current operation"),
    ("Ctrl+D", "Exit session"),
    ("Ctrl+O", "Toggle transcript view"),
    ("Up/Down", "Navigate history"),
    ("Tab", "Autocomplete / cycle suggestions"),
    ("Shift+Tab", "Cycle suggestions backward"),
    ("Esc", "Clear input / dismiss dialog"),
    ("Ctrl+R", "Search history"),
    ("Ctrl+L", "Clear screen"),
    ("Enter", "Submit message"),
    ("Shift+Enter", "New line"),
    ("Ctrl+U", "Clear line"),
    ("Ctrl+W", "Delete word backward"),
    ("Ctrl+A", "Move to start of line"),
    ("Ctrl+E", "Move to end of line"),
    ("Ctrl+K", "Delete to end of line"),
    ("Ctrl+B", "Background task"),
    ("Ctrl+G", "Toggle vim mode"),
]


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Show keyboard shortcuts, reading from config if available."""
    get_keybindings_config = context.get("get_keybindings_config")

    if get_keybindings_config:
        bindings = get_keybindings_config()
        if bindings:
            lines = ["## Keyboard Shortcuts", ""]
            for key, desc in bindings:
                lines.append(f"- **{key}** — {desc}")
            return {"type": "text", "value": "\n".join(lines)}

    # Default shortcuts
    lines = ["## Keyboard Shortcuts", ""]
    for key, desc in DEFAULT_KEYBINDINGS:
        lines.append(f"- **{key}** — {desc}")

    lines.extend(
        [
            "",
            "Customize in `~/.claude/keybindings.json`.",
        ]
    )

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
