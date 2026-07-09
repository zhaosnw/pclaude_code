#!/usr/bin/env python3
"""Normalize alignment runner output before comparison."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

STRIP_FIELDS = {
    "uuid",
    "timestamp",
    "session_id",
    "duration_ms",
    "absolute_path",
    "request_id",
    "agent_id",
    "phase1_notes",
}

REPLACE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\x1b\[[0-9;]*[a-zA-Z]"), ""),
    (
        re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        ),
        "<UUID>",
    ),
    (
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b"
        ),
        "<TIMESTAMP>",
    ),
]


def _normalize_string(value: str) -> str:
    for pattern, replacement in REPLACE_PATTERNS:
        value = pattern.sub(replacement, value)
    if os.path.isabs(value):
        return "<PATH>"
    return value


def normalize_value(value: Any, *, ignore_fields: set[str] | None = None) -> Any:
    if isinstance(value, dict):
        return normalize_event(value, ignore_fields=ignore_fields)
    if isinstance(value, list):
        return [normalize_value(item, ignore_fields=ignore_fields) for item in value]
    if isinstance(value, str):
        return _normalize_string(value)
    return value


def normalize_event(
    event: dict[str, Any] | Any,
    *,
    ignore_fields: set[str] | None = None,
) -> dict[str, Any] | Any:
    if not isinstance(event, dict):
        return event

    strip_fields = STRIP_FIELDS | (ignore_fields or set())
    normalized: dict[str, Any] = {}
    for key, value in event.items():
        if key in strip_fields:
            continue
        normalized[key] = normalize_value(value, ignore_fields=ignore_fields)
    return normalized


def normalize_result(
    result: dict[str, Any],
    *,
    ignore_fields: set[str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_event(result, ignore_fields=ignore_fields)
    assert isinstance(normalized, dict)
    return normalized


def normalize_jsonl(input_stream: Any, *, ignore_fields: set[str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    lines = input_stream.readlines() if hasattr(input_stream, "readlines") else input_stream
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            normalized = normalize_result(event, ignore_fields=ignore_fields)
            results.append(normalized)
        except json.JSONDecodeError:
            results.append({"raw": line})
    return results


def normalize_file(path: str, *, ignore_fields: set[str] | None = None) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return normalize_jsonl(handle, ignore_fields=ignore_fields)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Normalize alignment JSONL output")
    parser.add_argument("--input", "-i", default="-", help="Input JSONL file, default stdin")
    parser.add_argument("--output", "-o", default="-", help="Output JSONL file, default stdout")
    parser.add_argument("--ignore-fields", nargs="*", default=[], help="Extra object fields to strip")
    args = parser.parse_args()

    ignore_fields = set(args.ignore_fields)
    normalized = (
        normalize_jsonl(sys.stdin, ignore_fields=ignore_fields)
        if args.input == "-"
        else normalize_file(args.input, ignore_fields=ignore_fields)
    )

    out = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8")
    try:
        for item in normalized:
            out.write(json.dumps(item, ensure_ascii=False) + "\n")
    finally:
        if args.output != "-":
            out.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
