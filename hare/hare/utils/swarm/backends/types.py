"""
Swarm backend types.

Port of: src/utils/swarm/backends/types.ts
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

PaneBackendType = Literal["tmux", "iterm2"]


class PaneBackend(ABC):
    @property
    @abstractmethod
    def type(self) -> PaneBackendType: ...

    @abstractmethod
    async def create_pane(self, command: str, cwd: str) -> str: ...

    @abstractmethod
    async def kill_pane(self, pane_id: str) -> None: ...

    @abstractmethod
    async def send_keys(self, pane_id: str, keys: str) -> None: ...


class TeammateExecutor(ABC):
    @abstractmethod
    async def spawn(self, config: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def stop(self, agent_id: str) -> None: ...


@dataclass
class BackendDetectionResult:
    backend: PaneBackend
    is_native: bool = False
    needs_it2_setup: bool = False


def is_pane_backend(obj: Any) -> bool:
    """Check if an object is a PaneBackend instance."""
    return isinstance(obj, PaneBackend)
