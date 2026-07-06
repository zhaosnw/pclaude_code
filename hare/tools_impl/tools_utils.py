"""Port of: src/tools/utils.ts — message / tool-use ID helpers."""

from __future__ import annotations

from typing import Any


def tag_messages_with_tool_use_id(
    messages: list[dict[str, Any]],
    tool_use_id: str | None,
) -> list[dict[str, Any]]:
    if not tool_use_id:
        return messages
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("type") == "user":
            out.append({**m, "source_tool_use_id": tool_use_id})
        else:
            out.append(m)
    return out


def get_tool_use_id_from_parent_message(
    parent_message: dict[str, Any],
    tool_name: str,
) -> str | None:
    content = parent_message.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return None
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == tool_name
        ):
            bid = block.get("id")
            return str(bid) if bid is not None else None
    return None
