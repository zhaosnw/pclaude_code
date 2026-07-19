#!/usr/bin/env python3
"""Local Anthropic-compatible SSE server that replays a fixture.

Each POST to /v1/messages emits the next fixture response as a synthetic SSE
stream that the official Anthropic SDK can parse (message_start -> content
blocks -> message_delta -> message_stop).

NOTE (verified 2026-06-13): this is for driving the **TS reference** when
recording golden output. **hare itself does NOT use this** — hare's model
client uses ambient OAuth credentials and ignores ANTHROPIC_BASE_URL, so
pointing hare here would make a real, billed, nondeterministic call. hare's
deterministic path is Layer A (HARE_MODEL_FIXTURE in hare/testing/fake_model.py,
injected at production_deps()). Both sides consume the SAME fixture, so the
differential still holds: hare via Layer A, TS via this server.

Fixture format (shared with hare/testing/fake_model.py):
    {"kind": "scripted"|"replay",
     "responses": [{"stop_reason","content":[...blocks...],"usage":{...}}, ...]}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_MODEL = "claude-sonnet-4-20250514"


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def stream_response(resp: dict[str, Any]) -> bytes:
    """Render one fixture response as Anthropic-SDK-compatible SSE bytes."""
    blocks = resp.get("content", [])
    usage_in = resp.get("usage", {}).get("input_tokens", 0)
    usage_out = resp.get("usage", {}).get("output_tokens", 0)

    out = bytearray()
    out += _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": _MODEL,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": usage_in, "output_tokens": 0},
            },
        },
    )
    for idx, block in enumerate(blocks):
        btype = block.get("type")
        if btype == "text":
            out += _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            out += _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": block.get("text", "")},
                },
            )
        elif btype == "tool_use":
            out += _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id", f"toolu_{idx}"),
                        "name": block.get("name", ""),
                        "input": {},
                    },
                },
            )
            out += _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block.get("input", {})),
                    },
                },
            )
        else:  # passthrough for thinking / other block types
            out += _sse(
                "content_block_start",
                {"type": "content_block_start", "index": idx, "content_block": block},
            )
        out += _sse(
            "content_block_stop", {"type": "content_block_stop", "index": idx}
        )

    out += _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": resp.get("stop_reason", "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": usage_out},
        },
    )
    out += _sse("message_stop", {"type": "message_stop"})
    return bytes(out)


def make_server(fixture_path: str | Path, port: int = 0) -> ThreadingHTTPServer:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    responses = list(fixture["responses"])
    content_matched = fixture.get("kind") == "content-matched"
    cursor = {"i": 0}
    consumed: set[int] = set()
    import threading as _threading

    _lock = _threading.Lock()

    if content_matched:
        import sys as _sys

        _here = str(Path(__file__).resolve().parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from fixture_matching import select_response

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length) if length else b""

            with _lock:
                if content_matched:
                    try:
                        request = json.loads(body or b"{}")
                    except json.JSONDecodeError:
                        request = {}
                    selection = select_response(responses, request, consumed)
                    if selection is None:
                        # 400, not 500: the official Anthropic SDK's
                        # shouldRetry() retries on 408/409/429/>=500 (with
                        # backoff, default maxRetries=2). A fixture-author
                        # error — no response in the fixture matches this
                        # request — isn't a transient server hiccup, so a
                        # 5xx here used to make the SDK burn through its
                        # retry budget before the caller ever saw this
                        # message, surfacing as an opaque timeout instead.
                        self.send_error(400, "no fixture response matched request")
                        return
                    idx, resp = selection
                    if resp.get("once"):
                        consumed.add(idx)
                else:
                    i = cursor["i"]
                    if i >= len(responses):
                        # Same reasoning as above: fixture exhaustion is a
                        # fixture-author bug, not a retryable server error.
                        self.send_error(400, "fixture exhausted")
                        return
                    cursor["i"] = i + 1
                    resp = responses[i]
            payload = stream_response(resp)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_a: Any) -> None:  # silence
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    import sys

    fx = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8089
    srv = make_server(fx, port)
    print(f"mock anthropic server on http://127.0.0.1:{srv.server_address[1]}")
    srv.serve_forever()
