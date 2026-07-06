"""
Transport selection — choose WS/SSE/hybrid based on URL and env vars.

Port of: src/cli/transports/transportUtils.ts

Priority:
  1. SSETransport when CLAUDE_CODE_USE_CCR_V2 is set
  2. HybridTransport when CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2 is set
  3. WebSocketTransport — default
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse, ParseResult


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def get_transport_for_url(
    url_str: str,
    headers: dict[str, str] | None = None,
    session_id: str | None = None,
    refresh_headers: Any | None = None,
) -> Any:
    """Get the appropriate transport for a URL."""
    parsed = urlparse(url_str)
    hdrs = headers or {}

    # 1. CCR v2: SSE reads + POST writes
    if _env_truthy("CLAUDE_CODE_USE_CCR_V2"):
        from hare.cli.sse_transport import SSETransport

        sse_parsed = ParseResult(
            scheme="https" if parsed.scheme == "wss" else "http",
            netloc=parsed.netloc,
            path=parsed.path.rstrip("/") + "/worker/events/stream",
            params="",
            query="",
            fragment="",
        )
        return SSETransport(
            sse_parsed.geturl(),
            headers=hdrs,
            session_id=session_id,
            refresh_headers=refresh_headers,
        )

    # 2. WebSocket-based transports
    if parsed.scheme in ("ws", "wss"):
        if _env_truthy("CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2"):
            from hare.cli.hybrid_transport import HybridTransport

            return HybridTransport(
                url_str,
                headers=hdrs,
                session_id=session_id,
                refresh_headers=refresh_headers,
            )
        from hare.cli.websocket_transport import WebSocketTransport

        return WebSocketTransport(
            url_str,
            headers=hdrs,
            session_id=session_id,
            refresh_headers=refresh_headers,
        )

    raise ValueError(f"Unsupported protocol: {parsed.scheme}")
