"""
PR comments command.

Port of: src/commands/pr_comments/index.ts
"""

from __future__ import annotations

from typing import Any


COMMAND_NAME = "pr-comments"
DESCRIPTION = "Fetch or summarize PR review comments"
ALIASES: list[str] = []


async def run_pr_comments_command(_args: list[str]) -> dict[str, Any]:
    return {"ok": True, "comments": []}


async def call(args: str, **context: Any) -> dict[str, Any]:
    await run_pr_comments_command([])
    return {"type": "text", "value": "PR comments (SDK stub)."}
