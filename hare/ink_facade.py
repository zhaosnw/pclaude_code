"""
Ink/React terminal UI facade (port of src/ink.ts).

The recovered CLI is React/Ink-based; Python uses this module as a stub boundary.
"""

from __future__ import annotations

from typing import Any, Protocol


class Renderable(Protocol):
    def render(self) -> Any: ...


async def render(_node: Any, _options: Any | None = None) -> Any:
    """Stub: mount Ink tree."""
    return None


async def create_root(_options: Any | None = None) -> Any:
    return None
