"""Insert assistant blocks relative to tool_result blocks (`contentArray.ts`)."""

from __future__ import annotations

from typing import Any


def insert_block_after_tool_results(content: list[Any], block: Any) -> None:
    """Mutate content: insert after last tool_result, or before last block."""
    last_tool_result_index = -1
    for i, item in enumerate(content):
        if (
            item is not None
            and isinstance(item, dict)
            and item.get("type") == "tool_result"
        ):
            last_tool_result_index = i

    if last_tool_result_index >= 0:
        insert_pos = last_tool_result_index + 1
        content.insert(insert_pos, block)
        if insert_pos == len(content) - 1:
            content.append({"type": "text", "text": "."})
    else:
        insert_index = max(0, len(content) - 1)
        content.insert(insert_index, block)
