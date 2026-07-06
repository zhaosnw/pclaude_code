"""Port of: src/commands/passes/. API passes management."""

from typing import Any


async def call(args: list[str], context: Any) -> dict[str, Any]:
    return {"type": "text", "value": "API passes (stub)."}
