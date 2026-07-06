"""Port of: src/commands/fast/ — toggle fast mode."""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "fast"
DESCRIPTION = "Toggle fast mode (use faster/smaller model)"
ALIASES: list[str] = []


async def call(args: str, messages: list[dict[str, Any]], **ctx: Any) -> dict[str, Any]:
    subcommand = args.strip().lower()
    fast_enabled = _get_bool(ctx, "fast_mode", False)

    if subcommand in ("on", "enable"):
        _set_setting(ctx, "fast_mode", True)
        return {"type": "fast", "display_text": "Fast mode **enabled**."}
    elif subcommand in ("off", "disable"):
        _set_setting(ctx, "fast_mode", False)
        return {"type": "fast", "display_text": "Fast mode **disabled**."}
    else:
        status = "enabled" if fast_enabled else "disabled"
        return {"type": "fast",
                "display_text": f"Fast mode: **{status}**.\nUsage: /fast [on|off]"}


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
