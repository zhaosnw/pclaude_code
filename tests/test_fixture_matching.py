"""Unit tests for content-based fixture response matching."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fixture_matching import request_matches, select_response  # noqa: E402


def _user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant_tool(name: str, tid: str = "t1") -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": name, "id": tid}],
    }


def _tool_result(tid: str = "t1") -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tid, "content": "x"}],
    }


def test_last_tool_use_predicate() -> None:
    payload = {"messages": [_assistant_tool("Bash"), _tool_result()]}
    assert request_matches({"last_tool_use": "Bash"}, payload)
    assert not request_matches({"last_tool_use": "Read"}, payload)


def test_user_contains_predicate() -> None:
    payload = {"messages": [_user("please dispatch a background agent")]}
    assert request_matches({"user_contains": "dispatch"}, payload)
    assert not request_matches({"user_contains": "compact"}, payload)


def test_no_tool_result_predicate() -> None:
    fresh = {"messages": [_user("go")]}
    after = {"messages": [_assistant_tool("Bash"), _tool_result()]}
    assert request_matches({"no_tool_result": True}, fresh)
    assert not request_matches({"no_tool_result": True}, after)


def test_predicates_are_anded() -> None:
    payload = {"messages": [_user("create the file")]}
    assert request_matches({"no_tool_result": True, "user_contains": "create"}, payload)
    assert not request_matches(
        {"no_tool_result": True, "user_contains": "delete"}, payload
    )


def test_select_matches_by_content_not_order() -> None:
    responses = [
        {"match": {"last_tool_use": "Agent"}, "content": [{"type": "text", "text": "A"}]},
        {"match": {"last_tool_use": "Bash"}, "content": [{"type": "text", "text": "B"}]},
        {"content": [{"type": "text", "text": "fallback"}]},
    ]
    # Bash request selects the second response even though it comes second.
    after_bash = {"messages": [_assistant_tool("Bash"), _tool_result()]}
    idx, resp = select_response(responses, after_bash, set())
    assert resp["content"][0]["text"] == "B"
    # An unmatched request falls through to the wildcard.
    idx, resp = select_response(responses, {"messages": [_user("?")]}, set())
    assert resp["content"][0]["text"] == "fallback"


def test_once_response_is_consumed() -> None:
    responses = [
        {"match": {"user_contains": "go"}, "once": True, "content": [{"type": "text", "text": "first"}]},
        {"content": [{"type": "text", "text": "second"}]},
    ]
    payload = {"messages": [_user("go")]}
    idx, _ = select_response(responses, payload, set())
    assert idx == 0
    # After consuming index 0, the same request advances to the fallback.
    idx, resp = select_response(responses, payload, {0})
    assert resp["content"][0]["text"] == "second"


def test_normalizes_object_messages() -> None:
    # hare passes message objects, not dicts; matching must still work.
    class Inner:
        role = "user"
        content = [{"type": "text", "text": "dispatch it"}]

    class Msg:
        type = "user"
        message = Inner()

    payload = {"messages": [Msg()]}
    assert request_matches({"user_contains": "dispatch"}, payload)
