"""Port of: src/commands/release-notes/. Show latest release notes and changelog."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from hare.constants.product import PACKAGE_URL, VERSION, VERSION_CHANGELOG

COMMAND_NAME = "release-notes"
DESCRIPTION = "Show the latest release notes and changelog"
ALIASES: list[str] = ["changelog", "releases"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show current version, changelog, and recent GitHub releases."""
    arg = args[0].strip() if args else ""

    if arg in ("--help", "-h", "help"):
        return {"type": "text", "value": (
            f"Hare {VERSION}  —  release-notes\n\n"
            "Usage: /release-notes [tag]\n\n"
            "Without arguments, lists recent releases (requires `gh` CLI).\n"
            "With a tag (e.g. v2.1.88) shows details for that release.\n\n"
            f"Changelog: {VERSION_CHANGELOG}\n"
            f"Package  : {PACKAGE_URL}"
        )}

    async def _gh(*cmd: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", *cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout:
                return stdout.decode("utf-8", errors="replace").strip()
        except (FileNotFoundError, Exception):
            pass
        return None

    # Specific tag: fetch and display a single release
    if arg and not arg.startswith("-"):
        raw = await _gh("release", "view", arg, "--json", "name,body,publishedAt")
        if raw:
            try:
                rel = json.loads(raw)
                body = [f"## {rel.get('name', arg)}"]
                if rel.get("publishedAt"):
                    body.append(f"Published: {rel['publishedAt']}")
                body.append("")
                body.append(rel.get("body", "(no release notes)"))
                return {"type": "text", "value": "\n".join(body)}
            except json.JSONDecodeError:
                pass
        return {"type": "text", "value": (
            f"## {arg}\n\nCould not fetch details for `{arg}`.\n"
            f"View online: {VERSION_CHANGELOG}"
        )}

    # Default: list recent releases
    out = await _gh("release", "list", "--limit", "5",
                     "--exclude-drafts", "--exclude-pre-releases")
    if out:
        return {"type": "text", "value": "\n".join([
            f"Hare {VERSION}", "",
            "### Recent Releases",
            "```", out, "```", "",
            "Use `/release-notes <tag>` to view details.",
        ])}
    return {"type": "text", "value": "\n".join([
        f"Hare {VERSION}", "",
        "### Links",
        f"Changelog: {VERSION_CHANGELOG}",
        f"Package  : {PACKAGE_URL}",
        "",
        "Install the GitHub CLI to fetch releases directly:",
        "  https://cli.github.com/",
    ])}
