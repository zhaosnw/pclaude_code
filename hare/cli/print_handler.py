"""
Print mode — non-interactive SDK output formatting and stream processing.

Port of: src/cli/print.ts

Handles output formats: text, json, json-compact, stream-json (NDJSON).
Includes stream_results for consuming SDK message streams.
"""

from __future__ import annotations

import json as _json
import sys
from typing import Any, AsyncGenerator


def _extract_stream_text(msg: dict[str, Any]) -> str:
    """Extract printable text from a streaming SDK event."""
    event = msg.get("event")
    if not isinstance(event, dict):
        data = msg.get("data")
        return data.get("text", "") if isinstance(data, dict) else ""

    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta")
        if isinstance(delta, dict):
            return str(delta.get("text", "") or "")
    if event_type == "content_block_start":
        content_block = event.get("content_block")
        if isinstance(content_block, dict) and content_block.get("type") == "text":
            return str(content_block.get("text", "") or "")
    if event_type == "message_delta":
        delta = event.get("delta")
        if isinstance(delta, dict):
            return str(delta.get("text", "") or "")
    return ""


# ---- Output formatters ----


def print_json(data: Any) -> None:
    """Write JSON output (pretty-printed)."""
    print(_json.dumps(data, indent=2, default=str))


def print_json_compact(data: Any) -> None:
    """Write JSON output (compact, single line)."""
    print(_json.dumps(data, separators=(",", ":"), default=str))


def print_ndjson(data: Any) -> None:
    """Write a single NDJSON line to stdout."""
    from hare.cli.ndjson_safe_stringify import ndjson_safe_stringify

    line = ndjson_safe_stringify(data)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def print_text(data: Any) -> None:
    """Write plain text output."""
    if isinstance(data, str):
        print(data)
    elif isinstance(data, dict):
        text = data.get("data") or data.get("text") or data.get("result") or ""
        print(text)
    else:
        print(str(data))


def print_result(data: dict[str, Any], output_format: str = "text") -> None:
    """Print a query result in the requested format.

    Formats:
      - text: human-readable (default)
      - json: pretty-printed JSON
      - json-compact: single-line JSON
      - stream-json / ndjson: one NDJSON line
    """
    if output_format == "json":
        print_json(data)
    elif output_format == "json-compact":
        print_json_compact(data)
    elif output_format in ("stream-json", "ndjson"):
        print_ndjson(data)
    else:
        print_text(data)


def print_error(error: str, exit_code: int = 1, output_format: str = "text") -> None:
    """Print an error and optionally exit."""
    error_data = {"type": "error", "error": error, "exit_code": exit_code}
    if output_format in ("json", "json-compact", "stream-json", "ndjson"):
        print_result(error_data, output_format)
    else:
        print(f"Error: {error}", file=sys.stderr)


# ---- Stream consumer ----


async def stream_results(
    messages: AsyncGenerator[dict[str, Any], None],
    output_format: str = "stream-json",
) -> None:
    """Consume and print a stream of SDK query results.

    In stream-json mode: prints each message as NDJSON.
    In text mode: prints result text, errors to stderr.
    In json mode: buffers all messages, prints final result as JSON.
    """
    buffer: list[dict[str, Any]] = []

    async for msg in messages:
        if output_format == "stream-json":
            print_ndjson(msg)
        elif output_format in ("json", "json-compact"):
            buffer.append(msg)
        elif msg.get("type") == "result":
            text = msg.get("result", "")
            if text:
                print(text)
        elif msg.get("is_error"):
            errors = msg.get("errors", ["Unknown error"])
            print(f"Error: {errors[0] if errors else 'Unknown error'}", file=sys.stderr)
        elif msg.get("type") == "stream_event":
            text = _extract_stream_text(msg)
            if text:
                print(text, end="", flush=True)

    # Print buffered results for json format
    if buffer and output_format in ("json", "json-compact"):
        if output_format == "json":
            print_json(buffer)
        else:
            print_json_compact(buffer)
