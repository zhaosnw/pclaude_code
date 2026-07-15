"""Content-based fixture response matching.

Positional fixtures (``kind: scripted``) hand out responses in call order.
That breaks for concurrent flows — an async subagent and its parent both draw
from one response stream, and their requests interleave nondeterministically,
so response N is no longer meant for call N.

``kind: content-matched`` selects a response by inspecting the request instead.
Each response carries a ``match`` object; the first whose predicate holds for
the request is returned. A response may set ``"once": true`` to be consumed at
most once (so a repeated request advances to the next matching response).

Match predicates (all optional; all present ones must hold — AND):
    {
      "last_tool_use": "Bash",      # last assistant block is a tool_use named X
      "user_contains": "resume",    # some user text block contains this substring
      "no_tool_result": true,       # request has no tool_result (a fresh turn)
      "has_tool_result": true       # request contains a tool_result block
    }

Both the mock server (TS side) and hare's fake model (Layer A) import this, so
the two sides match identically against the same fixture.
"""

from __future__ import annotations

from typing import Any


def _normalize_message(msg: Any) -> dict[str, Any]:
    """Return ``{"role", "content"}`` for a message that may be an API dict
    (mock-server side) or a hare message object (Layer A side).

    hare passes UserMessage/AssistantMessage objects whose payload lives on
    ``.message`` (role, content). The TS side passes plain API dicts. Both
    reach the same predicates through this shim.
    """
    if isinstance(msg, dict):
        return {"role": msg.get("role"), "content": msg.get("content")}
    inner = getattr(msg, "message", None)
    if inner is not None:
        return {
            "role": getattr(inner, "role", getattr(msg, "type", None)),
            "content": getattr(inner, "content", None),
        }
    return {"role": getattr(msg, "type", None), "content": getattr(msg, "content", None)}


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    msgs = payload.get("messages")
    if not isinstance(msgs, list):
        return []
    return [_normalize_message(m) for m in msgs]


def _last_assistant_tool_use(payload: dict[str, Any]) -> str | None:
    for msg in reversed(_messages(payload)):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            return None
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                return name if isinstance(name, str) else None
        return None
    return None


def _iter_text(payload: dict[str, Any], role: str) -> list[str]:
    texts: list[str] = []
    for msg in _messages(payload):
        if msg.get("role") != role:
            continue
        content = msg.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
    return texts


def _has_block(payload: dict[str, Any], block_type: str) -> bool:
    for msg in _messages(payload):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == block_type:
                    return True
    return False


def request_matches(match: Any, payload: dict[str, Any]) -> bool:
    """Return True if every present predicate in ``match`` holds for the request."""
    if not isinstance(match, dict):
        return False

    want_tool = match.get("last_tool_use")
    if want_tool is not None and _last_assistant_tool_use(payload) != want_tool:
        return False

    sub = match.get("user_contains")
    if sub is not None and not any(sub in t for t in _iter_text(payload, "user")):
        return False

    if match.get("no_tool_result") and _has_block(payload, "tool_result"):
        return False

    if match.get("has_tool_result") and not _has_block(payload, "tool_result"):
        return False

    return True


def select_response(
    responses: list[dict[str, Any]],
    payload: dict[str, Any],
    consumed: set[int],
) -> tuple[int, dict[str, Any]] | None:
    """Pick the first matching, not-yet-exhausted response for a request.

    ``consumed`` tracks indices of responses marked ``"once": true`` that have
    already been served, so a repeated identical request advances rather than
    looping on the same response.

    Returns ``(index, response)`` or ``None`` if nothing matches.
    """
    for idx, resp in enumerate(responses):
        if idx in consumed:
            continue
        match = resp.get("match")
        # A response with no match clause is a wildcard fallback: it matches any
        # request. Place such entries last in the fixture.
        if match is None or request_matches(match, payload):
            return idx, resp
    return None
