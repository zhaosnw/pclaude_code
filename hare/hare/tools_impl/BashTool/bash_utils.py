"""Port of: src/tools/BashTool/utils.ts (subset — image / data-URI helpers)."""

from __future__ import annotations

import re
from typing import Any

_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.IGNORECASE)


def strip_empty_lines(content: str) -> str:
    lines = content.split("\n")
    start = 0
    while start < len(lines) and lines[start].strip() == "":
        start += 1
    end = len(lines) - 1
    while end >= 0 and lines[end].strip() == "":
        end -= 1
    if start > end:
        return ""
    return "\n".join(lines[start : end + 1])


def is_image_output(content: str) -> bool:
    return bool(re.match(r"^data:image/[a-z0-9.+_-]+;base64,", content, re.I))


def parse_data_uri(s: str) -> dict[str, str] | None:
    m = _DATA_URI_RE.match(s.strip())
    if not m:
        return None
    return {"media_type": m.group(1), "data": m.group(2)}


def build_image_tool_result(stdout: str, tool_use_id: str) -> dict[str, Any] | None:
    parsed = parse_data_uri(stdout)
    if not parsed:
        return None
    return {
        "tool_use_id": tool_use_id,
        "type": "tool_result",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": parsed["media_type"],
                    "data": parsed["data"],
                },
            }
        ],
    }
