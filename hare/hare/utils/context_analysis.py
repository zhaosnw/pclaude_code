"""Port of: src/utils/contextAnalysis.ts"""

from __future__ import annotations
from typing import Any
from hare.services.token_estimation import estimate_tokens


def analyze_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
    total_tokens = 0
    user_tokens = 0
    assistant_tokens = 0
    tool_tokens = 0
    for m in messages:
        t = m.get("type", "")
        c = m.get("message", {}).get("content", "")
        text = c if isinstance(c, str) else str(c)
        est = estimate_tokens(text)
        total_tokens += est
        if t == "user":
            user_tokens += est
        elif t == "assistant":
            assistant_tokens += est
    return {
        "total_tokens": total_tokens,
        "user_tokens": user_tokens,
        "assistant_tokens": assistant_tokens,
    }
