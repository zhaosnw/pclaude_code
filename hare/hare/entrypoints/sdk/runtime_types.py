"""
SDK runtime types.

Port of: src/entrypoints/sdk/runtimeTypes.ts
"""

from __future__ import annotations

from typing import Any, Literal


SDKEventType = Literal[
    "assistant_message",
    "tool_use",
    "tool_result",
    "system",
    "error",
    "result",
]


class SDKEvent:
    def __init__(
        self,
        type: SDKEventType = "system",
        data: Any = None,
        session_id: str = "",
    ) -> None:
        self.type = type
        self.data = data
        self.session_id = session_id
