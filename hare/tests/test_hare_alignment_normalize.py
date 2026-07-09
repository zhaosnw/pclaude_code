"""Tests for legacy_alignment/normalize.py strip non-deterministic fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add legacy_alignment/ to path for old Phase 1 normalization helpers.
_alignment_dir = str(Path(__file__).resolve().parents[2] / "legacy_alignment")
if _alignment_dir not in sys.path:
    sys.path.insert(0, _alignment_dir)

from normalize import (
    STRIP_FIELDS,
    normalize_event,
    normalize_jsonl,
)


def test_strips_known_fields() -> None:
    event = {
        "type": "assistant",
        "uuid": "abc-123-def-456",
        "timestamp": "2026-01-01T00:00:00Z",
        "session_id": "sess-001",
        "duration_ms": 1500,
        "hello": "world",
    }
    result = normalize_event(event)
    assert "hello" in result
    for field in STRIP_FIELDS:
        assert field not in result, f"Field '{field}' should be stripped"


def test_replaces_uuids_in_strings() -> None:
    event = {"text": "Generated uuid: 12345678-1234-1234-1234-123456789abc for you"}
    result = normalize_event(event)
    assert "<UUID>" in result["text"]
    assert "12345678" not in result["text"]


def test_replaces_timestamps_in_strings() -> None:
    event = {"log": "Event at 2026-01-15T14:30:00Z occurred"}
    result = normalize_event(event)
    assert "<TIMESTAMP>" in result["log"]
    assert "2026-01-15" not in result["log"]


def test_strips_ansi_escape_codes() -> None:
    event = {"output": "\x1b[32mOK\x1b[0m done"}
    result = normalize_event(event)
    assert "\x1b[32m" not in result["output"]
    assert "OK" in result["output"]


def test_normalizes_absolute_paths() -> None:
    event = {"path": "/home/user/project/src/main.py"}
    result = normalize_event(event)
    assert result["path"] == "<PATH>"


def test_keeps_relative_paths() -> None:
    event = {"path": "src/main.py"}
    result = normalize_event(event)
    assert result["path"] == "src/main.py"


def test_preserves_non_string_values() -> None:
    event = {"count": 42, "flag": True, "data": None, "nested": {"key": "val"}}
    result = normalize_event(event)
    assert result["count"] == 42
    assert result["flag"] is True
    assert result["data"] is None
    assert result["nested"] == {"key": "val"}


def test_normalize_nested_dicts() -> None:
    event = {
        "type": "tool_use",
        "data": {
            "uuid": "nested-uuid-123",
            "input": {"file": "/abs/path/file.txt"},
        },
    }
    result = normalize_event(event)
    assert "uuid" not in result["data"]
    assert result["data"]["input"]["file"] == "<PATH>"


def test_normalize_jsonl_stream() -> None:
    lines = [
        json.dumps({"type": "msg", "uuid": "a-b-c", "text": "hello"}),
        json.dumps({"type": "msg", "uuid": "d-e-f", "text": "world"}),
    ]
    results = normalize_jsonl(lines)
    assert len(results) == 2
    for r in results:
        assert "uuid" not in r
        assert "type" in r


def test_normalize_with_custom_ignore_fields() -> None:
    event = {"type": "msg", "custom_id": "xyz", "text": "hi"}
    result = normalize_event(event, ignore_fields={"custom_id"})
    assert "type" in result
    assert "custom_id" not in result


def test_handle_non_dict_event() -> None:
    result = normalize_event("not a dict")  # type: ignore
    assert result == "not a dict"


def test_handle_malformed_jsonl() -> None:
    results = normalize_jsonl(["not json", '{"valid": true}'])
    assert len(results) == 2
    assert results[0] == {"raw": "not json"}
    assert results[1] == {"valid": True}
