"""Port of: src/commands/terminal.ts. Show terminal environment and setup info."""

from __future__ import annotations

import os, platform, shutil, sys
from typing import Any

COMMAND_NAME = "terminal"
DESCRIPTION = "Show terminal environment and setup information"
ALIASES: list[str] = ["term"]

_CSI_U = {"ghostty": "Ghostty", "kitty": "Kitty", "iTerm.app": "iTerm2",
           "WezTerm": "WezTerm", "WarpTerminal": "Warp"}


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show terminal emulator, shell, size, color, keyboard protocol, and session context."""
    tp = os.environ.get("TERM_PROGRAM") or ""
    term = os.environ.get("TERM") or ""
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown"

    detected = tp
    if not detected:
        for key, name in _CSI_U.items():
            if key.lower() in term.lower():
                detected = name
                break
    detected = detected or term or "unknown"

    try:
        c, r = shutil.get_terminal_size()
        size_str = f"{c}x{r}"
    except (ValueError, OSError):
        size_str = "unknown"

    ct = os.environ.get("COLORTERM") or ""
    if ct in ("truecolor", "24bit"):
        color = "true color (24-bit)"
    elif "256color" in term:
        color = "256 colors"
    elif "color" in term:
        color = "ANSI colors"
    else:
        color = "basic"

    csi_u = "not detected"
    if tp in _CSI_U:
        csi_u = f"native ({_CSI_U[tp]})"
    else:
        for key, name in _CSI_U.items():
            if key.lower() in term.lower():
                csi_u = f"native ({name})"
                break

    ctx: list[str] = []
    if os.environ.get("TMUX"):        ctx.append("tmux")
    if os.environ.get("SSH_TTY"):     ctx.append("SSH")
    if not sys.stdin.isatty():        ctx.append("non-interactive")
    if os.environ.get("VSCODE_CWD"):  ctx.append("VSCode terminal")

    lines = [
        "Terminal Environment",
        "=" * 22,
        f"Terminal : {detected}",
        f"Shell    : {shell}",
        f"Size     : {size_str}",
        f"Color    : {color}",
        f"CSI u    : {csi_u}",
        f"Platform : {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python   : {sys.version.split()[0]}",
        f"Session  : {', '.join(ctx) if ctx else 'local interactive'}",
    ]

    if csi_u == "not detected":
        lines.append("")
        lines.append("Tip: Run /terminal-setup to configure Shift+Enter for multi-line input.\n"
                      "     Use backslash (\\) + Enter to insert newlines without setup.")

    return {"type": "text", "value": "\n".join(lines)}
