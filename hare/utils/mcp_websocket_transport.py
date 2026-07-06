"""MCP JSON-RPC over WebSocket — port of `mcpWebSocketTransport.ts`."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from hare.utils.diag_logs import log_for_diagnostics_no_pii  # type: ignore[import-not-found]

WS_CONNECTING = 0
WS_OPEN = 1


class WebSocketLike(Protocol):
    @property
    def ready_state(self) -> int: ...
    def close(self) -> None: ...
    def send(self, data: str) -> None: ...


def _parse_jsonrpc(data: str) -> Any:
    return json.loads(data)


class WebSocketTransport:
    def __init__(self, ws: WebSocketLike) -> None:
        self._ws = ws
        self._started = False
        self.onclose: Callable[[], None] | None = None
        self.onerror: Callable[[Exception], None] | None = None
        self.onmessage: Callable[[Any], None] | None = None
        self._opened = __import__("asyncio").get_event_loop().create_future()
        if ws.ready_state == WS_OPEN:
            self._opened.set_result(None)
        # Real impl attaches platform-specific open handlers here.

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("Start can only be called once per transport.")
        await self._opened
        if self._ws.ready_state != WS_OPEN:
            log_for_diagnostics_no_pii("error", "mcp_websocket_start_not_opened")
            raise RuntimeError("WebSocket is not open. Cannot start transport.")
        self._started = True

    async def close(self) -> None:
        if self._ws.ready_state in (WS_OPEN, WS_CONNECTING):
            self._ws.close()
        if self.onclose:
            self.onclose()

    async def send(self, message: Any) -> None:
        if self._ws.ready_state != WS_OPEN:
            log_for_diagnostics_no_pii("error", "mcp_websocket_send_not_opened")
            raise RuntimeError("WebSocket is not open. Cannot send message.")
        payload = json.dumps(message)
        self._ws.send(payload)
