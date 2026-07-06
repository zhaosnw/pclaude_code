"""Port of: src/commands/permissions/. Show/manage permission rules."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "permissions"
DESCRIPTION = "Show and manage permission rules"
ALIASES: list[str] = ["perms"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show permission rules."""
    ctx = context if isinstance(context, dict) else {}
    permission_context = ctx.get("permission_context", {})

    if isinstance(permission_context, dict):
        mode = permission_context.get("mode", "default")
        deny_rules = permission_context.get("deny_rules", [])
        ask_rules = permission_context.get("ask_rules", [])
        allow_rules = permission_context.get("allow_rules", [])
    else:
        mode = getattr(permission_context, "mode", "default")
        deny_rules = getattr(permission_context, "deny_rules", [])
        ask_rules = getattr(permission_context, "ask_rules", [])
        allow_rules = getattr(permission_context, "allow_rules", [])

    lines = [f"# Permission Mode: {mode}\n"]

    if deny_rules:
        lines.append("## Deny Rules")
        for r in deny_rules:
            lines.append(f"  - `{r}`")
        lines.append("")

    if allow_rules:
        lines.append("## Allow Rules")
        for r in allow_rules:
            lines.append(f"  - `{r}`")
        lines.append("")

    if ask_rules:
        lines.append("## Ask Rules")
        for r in ask_rules:
            lines.append(f"  - `{r}`")
        lines.append("")

    if not deny_rules and not allow_rules and not ask_rules:
        lines.append("No custom permission rules configured.")
        lines.append("")
        lines.append("Add rules in ~/.claude/settings.json:")
        lines.append('  "permissions": {')
        lines.append('    "deny": ["Bash rm -rf /"],')
        lines.append('    "allow": ["Bash git *"],')
        lines.append('    "ask": ["WebFetch *"]')
        lines.append("  }")

    lines.append("")
    lines.append(f"Modes: default, acceptEdits, plan, bypass")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "argument_hint": "",
    }
