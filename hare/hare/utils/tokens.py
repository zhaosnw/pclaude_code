"""Port of: src/utils/tokens.ts"""

from __future__ import annotations
from typing import Any, Optional
from hare.services.token_estimation import estimate_tokens


def get_token_usage(messages: list[dict[str, Any]]) -> dict[str, int]:
    total = 0
    for m in messages:
        c = m.get("message", {}).get("content", "")
        total += estimate_tokens(c if isinstance(c, str) else str(c))
    return {"total": total, "estimated": True}


def token_count_with_estimation(text: str) -> int:
    return estimate_tokens(text)


def token_count_from_last_api_response(response: dict[str, Any]) -> dict[str, int]:
    usage = response.get("usage", {})
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


def _get_message_usage(msg: Any) -> Optional[dict[str, Any]]:
    """Extract usage dict from a message, walking back through content."""
    if msg is None:
        return None
    m = getattr(msg, "message", None)
    if m is None:
        return None
    return getattr(m, "usage", None) or (
        getattr(m, "usage", None) if hasattr(m, "usage") else None
    )


def final_context_tokens_from_last_response(messages: list[Any]) -> int:
    """Final context window size from the last API response's usage.iterations[-1].

    Mirrors TS finalContextTokensFromLastResponse (tokens.ts L79-L104):
    walks back from end of messages to find usage, uses iterations[-1] if
    present, falls back to top-level input_tokens + output_tokens (no cache).
    """
    i = len(messages) - 1
    while i >= 0:
        msg = messages[i]
        usage = _get_message_usage(msg)
        if usage:
            iterations = usage.get("iterations")
            if isinstance(iterations, list) and len(iterations) > 0:
                last = iterations[-1]
                return int(last.get("input_tokens", 0)) + int(
                    last.get("output_tokens", 0)
                )
            return int(usage.get("input_tokens", 0)) + int(
                usage.get("output_tokens", 0)
            )
        i -= 1
    return 0
