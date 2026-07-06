"""
LSP server manager.

Port of: src/services/lsp/
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LspServerManager:
    _servers: dict[str, Any] = field(default_factory=dict)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._servers.clear()

    async def send_request(self, method: str, params: dict[str, Any]) -> Any:
        return None

    async def did_open(self, uri: str, language: str, content: str) -> None:
        pass

    async def did_close(self, uri: str) -> None:
        pass


_instance: LspServerManager | None = None


def get_lsp_server_manager() -> LspServerManager:
    global _instance
    if _instance is None:
        _instance = LspServerManager()
    return _instance
