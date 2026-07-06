#!/usr/bin/env python3
"""Recording proxy: forward an Anthropic-compatible CLI's traffic to a real
upstream (e.g. DeepSeek) while capturing /v1/messages responses into a fixture.

Why: the recovered TS reference (2.1.88) hangs against a static mock (its Grove
subscription probe never resolves), but runs fine against a real upstream that
returns fast errors for unknown endpoints. So we forward everything to the real
API, and tee the model's streamed /v1/messages responses into a fixture that
hare can replay deterministically via Layer A. Same model output on both sides
=> a true exact-version differential.

Usage:
    UPSTREAM=https://api.deepseek.com/anthropic \
    CAPTURE=/path/to/fixture.json \
    python scripts/recording_proxy.py <port>

Point the CLI at it: ANTHROPIC_BASE_URL=http://127.0.0.1:<port> (the CLI keeps
sending its own auth headers, which we forward upstream unchanged).
"""

from __future__ import annotations

import json
import os
import sys
from http.client import HTTPSConnection, HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

UPSTREAM = os.environ.get("UPSTREAM", "https://api.deepseek.com/anthropic")
CAPTURE = os.environ.get("CAPTURE", "/tmp/captured_fixture.json")
_up = urlparse(UPSTREAM)
_UP_HOST = _up.hostname or "api.deepseek.com"
_UP_PORT = _up.port or (443 if _up.scheme == "https" else 80)
_UP_BASE = _up.path.rstrip("/")  # e.g. "/anthropic"

_captured: list[dict] = []


def _upstream_conn():
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy and _up.scheme == "https":
        p = urlparse(proxy)
        conn = HTTPSConnection(p.hostname, p.port or 80)
        conn.set_tunnel(_UP_HOST, _UP_PORT)
        return conn
    if _up.scheme == "https":
        return HTTPSConnection(_UP_HOST, _UP_PORT)
    return HTTPConnection(_UP_HOST, _UP_PORT)


def _parse_sse_to_response(raw: bytes) -> dict | None:
    """Assemble streamed Anthropic SSE bytes into one fixture response."""
    blocks: dict[int, dict] = {}
    stop_reason = "end_turn"
    usage = {"input_tokens": 0, "output_tokens": 0}
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "message_start":
            usage["input_tokens"] = (
                ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
            )
        elif t == "content_block_start":
            idx = ev.get("index", 0)
            cb = ev.get("content_block", {})
            if cb.get("type") == "text":
                blocks[idx] = {"type": "text", "text": cb.get("text", "")}
            elif cb.get("type") == "tool_use":
                blocks[idx] = {
                    "type": "tool_use",
                    "id": cb.get("id", f"toolu_{idx}"),
                    "name": cb.get("name", ""),
                    "input": "",
                }
        elif t == "content_block_delta":
            idx = ev.get("index", 0)
            d = ev.get("delta", {})
            if d.get("type") == "text_delta" and idx in blocks:
                blocks[idx]["text"] = blocks[idx].get("text", "") + d.get("text", "")
            elif d.get("type") == "input_json_delta" and idx in blocks:
                blocks[idx]["input"] = blocks[idx].get("input", "") + d.get(
                    "partial_json", ""
                )
        elif t == "message_delta":
            if ev.get("delta", {}).get("stop_reason"):
                stop_reason = ev["delta"]["stop_reason"]
            if ev.get("usage", {}).get("output_tokens") is not None:
                usage["output_tokens"] = ev["usage"]["output_tokens"]
    content = []
    for idx in sorted(blocks):
        b = blocks[idx]
        if b["type"] == "tool_use":
            try:
                b = {**b, "input": json.loads(b["input"] or "{}")}
            except json.JSONDecodeError:
                b = {**b, "input": {}}
        content.append(b)
    if not content:
        return None
    return {"stop_reason": stop_reason, "content": content, "usage": usage}


def _save():
    fixture = {"kind": "replay", "responses": _captured}
    with open(CAPTURE, "w", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False, indent=2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length) if length else b""
        # Forward to upstream, preserving the client's auth headers.
        hop = {"host", "content-length", "connection", "accept-encoding"}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in hop}
        headers["Host"] = _UP_HOST
        headers["Accept-Encoding"] = "identity"
        conn = _upstream_conn()
        try:
            conn.request(method, _UP_BASE + self.path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except Exception as exc:  # upstream unreachable
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(exc).encode())
            return
        # Capture model responses
        if self.path.endswith("/v1/messages") and resp.status == 200:
            parsed = _parse_sse_to_response(raw)
            if parsed:
                _captured.append(parsed)
                _save()
        # Relay upstream response to the client verbatim
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("transfer-encoding", "connection", "content-encoding"):
                continue
            self.send_header(k, v)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")

    def log_message(self, *_a) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"recording proxy on http://127.0.0.1:{srv.server_address[1]} -> {UPSTREAM}")
    print(f"capturing /v1/messages to {CAPTURE}")
    srv.serve_forever()
