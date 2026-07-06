"""
SessionsWebSocket – WebSocket client for remote sessions.

Port of: src/remote/SessionsWebSocket.ts
"""

from __future__ import annotations
import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class SessionsWebSocketCallbacks:
    on_message: Callable[[dict[str, Any]], None] | None = None
    on_control_request: Callable[[dict[str, Any]], None] | None = None
    on_control_cancel: Callable[[dict[str, Any]], None] | None = None
    on_control_response: Callable[[dict[str, Any]], None] | None = None
    on_close: Callable[[int, str], None] | None = None
    on_error: Callable[[Exception], None] | None = None


class SessionsWebSocket:
    def __init__(
        self,
        session_id: str,
        access_token: str,
        organization_uuid: str,
        base_url: str = "wss://api.anthropic.com",
        callbacks: SessionsWebSocketCallbacks | None = None,
    ):
        self.session_id = session_id
        self.access_token = access_token
        self.organization_uuid = organization_uuid
        self.base_url = base_url
        self.callbacks = callbacks or SessionsWebSocketCallbacks()
        self._ws: Any = None
        self._connected = False
        self._reconnect_attempts = 0
        self._max_reconnect = 10
        self._ping_interval = 30.0

    @property
    def url(self) -> str:
        base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/v1/sessions/ws/{self.session_id}/subscribe?organization_uuid={self.organization_uuid}"

    async def connect(self) -> None:
        self._connected = True
        self._reconnect_attempts = 0

    async def close(self) -> None:
        self._connected = False
        if self._ws:
            self._ws = None

    async def reconnect(self) -> None:
        if self._reconnect_attempts >= self._max_reconnect:
            return
        self._reconnect_attempts += 1
        backoff = min(2**self._reconnect_attempts, 60)
        await asyncio.sleep(backoff)
        await self.connect()

    def is_connected(self) -> bool:
        return self._connected

    async def send_control_response(
        self, request_id: str, data: dict[str, Any]
    ) -> None:
        msg = {"type": "control_response", "request_id": request_id, **data}
        await self._send_json(msg)

    async def send_control_request(self, data: dict[str, Any]) -> str:
        req_id = str(uuid.uuid4())
        msg = {"type": "control_request", "request_id": req_id, **data}
        await self._send_json(msg)
        return req_id

    async def _send_json(self, data: dict[str, Any]) -> None:
        pass

    def _handle_frame(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        msg_type = msg.get("type", "")
        if msg_type == "control_request" and self.callbacks.on_control_request:
            self.callbacks.on_control_request(msg)
        elif msg_type == "control_cancel_request" and self.callbacks.on_control_cancel:
            self.callbacks.on_control_cancel(msg)
        elif msg_type == "control_response" and self.callbacks.on_control_response:
            self.callbacks.on_control_response(msg)
        elif self.callbacks.on_message:
            self.callbacks.on_message(msg)
