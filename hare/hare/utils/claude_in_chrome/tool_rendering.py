"""Tool result rendering for Chrome extension (React in TS).

Port of: src/utils/claudeInChrome/toolRendering.tsx
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolRenderModel:
    tool_name: str
    payload: dict[str, Any]


def render_tool_for_chrome(model: ToolRenderModel) -> str:
    """Return plain-text fallback for tool UI."""
    return f"[{model.tool_name}] {model.payload!r}"
