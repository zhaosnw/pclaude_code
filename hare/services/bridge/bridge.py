"""
Bridge service – communicates with external IDEs/extensions via socket/pipe.

Port of: src/services/bridge/bridgeService.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BridgeService:
    port: int | None = None
    _server: Any = None
    _handlers: dict[str, Callable] = field(default_factory=dict)

    async def start(self, port: int = 0) -> int:
        """Start bridge server. Stub returns port."""
        self.port = port or 9876
        return self.port

    async def stop(self) -> None:
        self.port = None
        self._server = None

    def on(self, event: str, handler: Callable) -> None:
        self._handlers[event] = handler

    async def send(self, event: str, data: Any = None) -> None:
        """Send event to connected client. Stub."""
        pass

    @property
    def is_connected(self) -> bool:
        return self._server is not None


_instance: BridgeService | None = None


def get_bridge_service() -> BridgeService:
    global _instance
    if _instance is None:
        _instance = BridgeService()
    return _instance
