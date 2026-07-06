"""Port of: src/entrypoints/init.ts"""

from __future__ import annotations
from typing import Any


async def initialize_app(options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = options or {}
    from hare.bootstrap.state import get_session_id
    from hare.services.analytics.growthbook import init_growthbook

    init_growthbook()
    return {"session_id": get_session_id(), "initialized": True}
