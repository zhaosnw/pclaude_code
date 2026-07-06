"""
HTTP server – provides REST/WebSocket API for SDK and remote connections.

Port of: src/server/httpServer.ts
"""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response({"status": "ok"})
        elif self.path == "/api/status":
            self._json_response({"running": True})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        if self.path == "/api/message":
            self._json_response({"received": True})
        else:
            self.send_error(404)

    def _json_response(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def create_server(host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    server = HTTPServer((host, port), _Handler)
    return server


async def run_server_async(
    host: str = "127.0.0.1", port: int = 0
) -> tuple[HTTPServer, int]:
    """Run the HTTP server in a background thread."""
    server = create_server(host, port)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port
