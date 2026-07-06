"""Port of: src/commands/files.ts. List files referenced in the conversation."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

COMMAND_NAME = "files"
DESCRIPTION = "List files referenced in the conversation"
ALIASES: list[str] = ["lsf", "listfiles"]


_FILE_RE = re.compile(
    r"""
    (?:(?:^|[^\w/.-])\(?(?:[\w@~-]+[/\\])+[\w@.~\[\]-]+\.\w{1,10}\)?)|  # paths with dirs
    (?:(?:^|[^\w/.-])\(?\w:[/\\][\w@.~\[\] /\\-]+\.\w{1,10}\)?)|            # Windows absolute
    (?:(?:^|[^\w/.-])/?[\w@.~_-]+\.(?:py|ts|js|rs|go|java|rb|cpp|c|h|sh|bat|
      ps1|yaml|yml|json|toml|xml|html|css|scss|md|txt|cfg|ini|conf|
      env|tf|sql|graphql|proto|lua|swift|kt|scala|r|jl|dart|ex|exs|
      eex|heex|leex|vue|svelte|jsx|tsx)[ \b,;)\]'"$])\b\)?               # bare filenames
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_files(messages: list[dict[str, Any]]) -> set[str]:
    """Pull file paths out of conversation message content."""
    found: set[str] = set()
    for msg in messages:
        # Handle dict-shaped and object-shaped messages
        content = msg.get("message", msg).get("content", "")
        if isinstance(content, str):
            for m in _FILE_RE.finditer(content):
                matched = m.group().strip().rstrip(",;)]\"'")
                if matched:
                    found.add(matched)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text", "") or block.get("content", "")
                if isinstance(text, str):
                    for m in _FILE_RE.finditer(text):
                        matched = m.group().strip().rstrip(",;)]\"'")
                        if matched:
                            found.add(matched)
    return found


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """List files referenced in the conversation, with optional filtering.

    Supports:
      --exists     Only show files that exist on disk
      --ext <.ext>  Filter by file extension (e.g. --ext py)
      --count      Show summary counts instead of listing every file
      --cwd <dir>  Set working directory for existence checks
    """
    if isinstance(context, dict):
        messages: list[dict[str, Any]] = context.get("messages", [])
    else:
        messages = getattr(context, "messages", []) or []

    files = _extract_files(messages)

    only_exists = "--exists" in args
    count_mode = "--count" in args
    ext_filter: str | None = None
    cwd = os.getcwd()

    for i, a in enumerate(args):
        if a in ("--ext", "-e") and i + 1 < len(args):
            ext_filter = args[i + 1].lstrip(".").lower()
        elif a.startswith("--ext="):
            ext_filter = a.split("=", 1)[1].lstrip(".").lower()
        elif a in ("--cwd", "-C") and i + 1 < len(args):
            cwd = os.path.expanduser(args[i + 1])
        elif a.startswith("--cwd="):
            cwd = os.path.expanduser(a.split("=", 1)[1])

    # Extension filter
    if ext_filter:
        files = {f for f in files if f.rsplit(".", 1)[-1].lower() == ext_filter}

    # Existence check
    if only_exists:
        extant: set[str] = set()
        for f in files:
            full = os.path.join(cwd, f) if not os.path.isabs(f) else f
            if os.path.isfile(full):
                extant.add(f)
        files = extant

    if not files:
        return {"type": "text", "value": "No files referenced."}

    # Count mode: summary grouped by extension
    if count_mode:
        ext_counts = Counter(f.rsplit(".", 1)[-1].lower() if "." in f else "(no ext)" for f in files)
        lines = [f"{len(files)} file(s) referenced:"]
        for ext, cnt in ext_counts.most_common():
            lines.append(f"  {ext}: {cnt}")
        return {"type": "text", "value": "\n".join(lines)}

    # Default: sorted listing
    sorted_files = sorted(files, key=str.lower)
    lines = sorted_files
    header = f"{len(sorted_files)} file(s) referenced in conversation:"
    display = header + "\n" + "\n".join(f"  {f}" for f in lines)
    return {"type": "files", "files": sorted_files, "display_text": display}
