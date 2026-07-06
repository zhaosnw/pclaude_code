"""Port of: src/commands/todo.ts — toggle todo write behavior."""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "todo"
DESCRIPTION = "Toggle todo write behavior"
ALIASES = ["tasks"]


async def call(args: str, messages: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
    subcommand = args.strip().lower()
    todo_write = _get_bool(context, "todo_write_enabled", True)

    if subcommand in ("on", "enable"):
        _set_setting(context, "todo_write_enabled", True)
        return {"type": "todo", "display_text": "Todo write mode: **enabled**."}
    elif subcommand in ("off", "disable"):
        _set_setting(context, "todo_write_enabled", False)
        return {"type": "todo", "display_text": "Todo write mode: **disabled**."}
    else:
        status = "enabled" if todo_write else "disabled"
        return {"type": "todo",
                "display_text": f"Todo write mode: **{status}**.\nUsage: /todo [on|off]"}


def _get_bool(ctx: dict[str, Any], key: str, default: bool) -> bool:
    val = ctx.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "enabled", "on", "yes")
    return bool(val)


def _set_setting(ctx: dict[str, Any], key: str, value: Any) -> None:
    for k in ("set_setting", "setSetting"):
        setter = ctx.get(k)
        if setter and callable(setter):
            try:
                result = setter(key, value)
                import asyncio
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                pass
