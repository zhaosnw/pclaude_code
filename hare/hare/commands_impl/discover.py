"""Port of: src/commands/discover.ts. Discover project structure and available commands."""

from __future__ import annotations

import os
from typing import Any

from hare.commands_impl import get_all_command_definitions

COMMAND_NAME = "discover"
DESCRIPTION = "Discover available features and commands"
ALIASES: list[str] = ["list-commands", "explore"]

_MARKERS = {
    "pyproject.toml": "Python", "package.json": "Node.js", "go.mod": "Go",
    "Cargo.toml": "Rust", "Makefile": "Make", "Dockerfile": "Docker",
    ".github": "GitHub CI/CD", "CLAUDE.md": "Claude Code", ".claude": "Claude config",
}

# keyword -> category; more-specific keywords listed first
_KW_CAT: dict[str, str] = {}
for _cat, _kws in [
    ("Session", "session clear compact context cost export resume rename summary share stash rewind".split()),
    ("Project & Git", "commit diff pr branch init worktree files discover doctor".split()),
    ("Review", "review security-review bughunter autofix".split()),
    ("AI", "plan ultraplan fast agent advisor todo".split()),
    ("Config & Tools", "config theme model permissions hooks keybindings mcp plugin ide terminal".split()),
]:
    for _kw in _kws:
        _KW_CAT.setdefault(_kw, _cat)

_ORDER = ["Session", "Project & Git", "Review", "AI", "Config & Tools", "Other"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Discover available commands and project structure."""
    cmds = get_all_command_definitions()
    cwd = os.getcwd()
    if isinstance(context, dict) and callable(context.get("get_original_cwd")):
        try:
            cwd = context["get_original_cwd"]()
        except Exception:
            pass

    markers: list[str] = []
    try:
        for entry in sorted(os.listdir(cwd)):
            full = os.path.join(cwd, entry)
            if entry in _MARKERS:
                markers.append(f"  {entry}  →  {_MARKERS[entry]}")
            elif entry in {"src", "tests", "docs", "scripts", ".git"} and os.path.isdir(full):
                markers.append(f"  📁 {entry}")
    except (OSError, PermissionError):
        pass

    categorized: dict[str, list[dict[str, Any]]] = {}
    for cmd in cmds:
        text = (cmd["name"] + " " + cmd.get("description", "")).lower()
        cat = "Other"
        for kw, c in _KW_CAT.items():
            if kw in text:
                cat = c
                break
        categorized.setdefault(cat, []).append(cmd)

    lines = [f"Working directory: `{cwd}`", f"Commands: {len(cmds)}"]
    if markers:
        lines.append("\n" + "\n".join(markers))
    for cat in _ORDER:
        cat_cmds = categorized.get(cat)
        if not cat_cmds:
            continue
        lines.append(f"\n**{cat}** ({len(cat_cmds)})")
        for cmd in sorted(cat_cmds, key=lambda c: c["name"]):
            a = ""
            if cmd.get("aliases"):
                a = " (aka " + ", ".join(cmd["aliases"]) + ")"
            lines.append(f"  /{cmd['name']}{a} — {cmd['description']}")
    lines.append("\nTip: `/help <cmd>` for detailed help on any command.")
    return {"type": "discover", "display_text": "\n".join(lines)}
