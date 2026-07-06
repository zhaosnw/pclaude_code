"""
RemoteSessionManager – coordinates WebSocket and permission flows.

Port of: src/remote/RemoteSessionManager.ts
"""

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from hare.remote.sessions_websocket import SessionsWebSocket, SessionsWebSocketCallbacks


@dataclass
class RemoteSessionConfig:
    session_id: str = ""
    access_token: str = ""
    organization_uuid: str = ""
    base_url: str = "https://api.anthropic.com"


@dataclass
class RemoteSessionCallbacks:
    on_message: Callable[[dict[str, Any]], None] | None = None
    on_permission_request: Callable[[dict[str, Any]], None] | None = None
    on_disconnect: Callable[[], None] | None = None


class RemoteSessionManager:
    def __init__(
        self,
        config: RemoteSessionConfig,
        callbacks: RemoteSessionCallbacks | None = None,
    ):
        self.config = config
        self.callbacks = callbacks or RemoteSessionCallbacks()
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._ws: SessionsWebSocket | None = None

    async def connect(self) -> None:
        ws_callbacks = SessionsWebSocketCallbacks(
            on_message=self._on_ws_message,
            on_control_request=self._on_control_request,
            on_control_cancel=self._on_control_cancel,
            on_close=self._on_close,
        )
        self._ws = SessionsWebSocket(
            session_id=self.config.session_id,
            access_token=self.config.access_token,
            organization_uuid=self.config.organization_uuid,
            base_url=self.config.base_url,
            callbacks=ws_callbacks,
        )
        await self._ws.connect()

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_message(self, content: str) -> bool:
        from hare.utils.teleport.api import send_event_to_remote_session

        return await send_event_to_remote_session(self.config.session_id, content)

    async def respond_to_permission_request(
        self, request_id: str, approved: bool
    ) -> None:
        if self._ws:
            await self._ws.send_control_response(request_id, {"approved": approved})

    async def cancel_session(self) -> None:
        if self._ws:
            await self._ws.send_control_request({"type": "interrupt"})

    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.is_connected()

    def get_session_id(self) -> str:
        return self.config.session_id

    async def reconnect(self) -> None:
        if self._ws:
            await self._ws.reconnect()

    def _on_ws_message(self, msg: dict[str, Any]) -> None:
        if self.callbacks.on_message:
            self.callbacks.on_message(msg)

    def _on_control_request(self, msg: dict[str, Any]) -> None:
        if self.callbacks.on_permission_request:
            self.callbacks.on_permission_request(msg)

    def _on_control_cancel(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("request_id", "")
        if req_id in self._pending_permissions:
            self._pending_permissions.pop(req_id, None)

    def _on_close(self, code: int, reason: str) -> None:
        if self.callbacks.on_disconnect:
            self.callbacks.on_disconnect()
