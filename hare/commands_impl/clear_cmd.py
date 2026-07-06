"""Port of: src/commands/clear/. Clear conversation history."""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "clear"
DESCRIPTION = "Clear the current conversation history"
ALIASES: list[str] = []


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Clear conversation by resetting messages."""
    ctx = context if isinstance(context, dict) else {}
    cleared = False

    for fn_name in ("clear_messages", "clearMessages"):
        fn = ctx.get(fn_name)
        if fn and callable(fn):
            try:
                result = fn()
                import asyncio
                if asyncio.iscoroutine(result):
                    await result
                cleared = True
            except Exception:
                pass

    if not cleared:
        for fn_name in ("set_messages", "setMessages"):
            fn = ctx.get(fn_name)
            if fn and callable(fn):
                try:
                    result = fn([])
                    import asyncio
                    if asyncio.iscoroutine(result):
                        await result
                    cleared = True
                except Exception:
                    pass

    msg = "Conversation cleared." if cleared else "Conversation cleared (no active session)."
    return {"type": "text", "value": msg}
