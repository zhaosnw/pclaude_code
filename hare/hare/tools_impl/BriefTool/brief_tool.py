"""
BriefTool – send a message to the user.

Port of: src/tools/BriefTool/BriefTool.ts
"""

from __future__ import annotations
import time
import os
from typing import Any

from hare.tools_impl.BriefTool.prompt import BRIEF_TOOL_NAME, LEGACY_BRIEF_TOOL_NAME

TOOL_NAME = BRIEF_TOOL_NAME
ALIASES = [LEGACY_BRIEF_TOOL_NAME]


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The message to send"},
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File paths to attach",
            },
            "status": {
                "type": "string",
                "enum": ["normal", "proactive"],
                "description": "Message intent",
            },
        },
        "required": ["message"],
    }


async def resolve_attachments(paths: list[str], cwd: str) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(cwd, p)
        resolved.append({"path": full, "exists": os.path.exists(full)})
    return resolved


async def call(
    message: str,
    attachments: list[str] | None = None,
    status: str = "normal",
    cwd: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"message": message, "sentAt": time.time()}
    if attachments:
        result["attachments"] = await resolve_attachments(
            attachments, cwd or os.getcwd()
        )
    return result
