"""Direct-connect WebSocket session manager (port of src/server/directConnectManager.ts)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, NotRequired, TypedDict


class DirectConnectConfig(TypedDict):
    server_url: str
    session_id: str
    ws_url: str
    auth_token: NotRequired[str | None]


class DirectConnectCallbacks(TypedDict, total=False):
    on_message: Callable[[Any], None]
    on_permission_request: Callable[[Any, str], None]
    on_connected: Callable[[], None]
    on_disconnected: Callable[[], None]
    on_error: Callable[[Exception], None]


class DirectConnectSessionManager:
    def __init__(
        self, config: DirectConnectConfig, callbacks: DirectConnectCallbacks
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        self._ws: Any | None = None

    def connect(self) -> None:
        self._callbacks.get("on_connected", lambda: None)()

    def send_message(self, _content: Any) -> bool:
        return False

    def respond_to_permission_request(self, _request_id: str, _result: Any) -> None:
        return

    def send_interrupt(self) -> None:
        _ = json.dumps(
            {
                "type": "control_request",
                "request_id": str(uuid.uuid4()),
                "request": {"subtype": "interrupt"},
            }
        )

    def disconnect(self) -> None:
        self._ws = None

    def is_connected(self) -> bool:
        return False
