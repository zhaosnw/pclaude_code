"""Port of: src/commands/privacy-settings/"""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "privacy-settings"
DESCRIPTION = "View or change privacy settings"
ALIASES = ["privacy"]


async def call(args: str, messages: list[dict[str, Any]], **ctx: Any) -> dict[str, Any]:
    subcommand = args.strip().lower()
    analytics = _get_bool(ctx, "analytics_enabled", True)
    telemetry = _get_bool(ctx, "telemetry_enabled", True)

    if not subcommand:
        return {"type": "privacy",
                "display_text": f"# Privacy Settings\n\n"
                                f"- **Analytics**: {'Enabled' if analytics else 'Disabled'}\n"
                                f"- **Telemetry**: {'Enabled' if telemetry else 'Disabled'}\n\n"
                                f"Use `/privacy toggle <setting>` to change."}

    if subcommand in ("analytics", "telemetry", "data-collection", "toggle analytics", "toggle telemetry"):
        target = subcommand.replace("toggle ", "")
        key_map = {"analytics": "analytics_enabled", "telemetry": "telemetry_enabled",
                   "data-collection": "data_collection"}
        ctx_key = key_map.get(target, f"{target}_enabled")
        old = _get_bool(ctx, ctx_key, target != "data-collection")
        new_val = not old
        _set_bool(ctx, ctx_key, new_val)
        status = "enabled" if new_val else "disabled"
        return {"type": "privacy",
                "display_text": f"{target.replace('-', ' ').title()} is now **{status}**."}

    return {"type": "error",
            "display_text": "Usage: /privacy [analytics|telemetry|data-collection]"}


def _get_bool(ctx: dict[str, Any], key: str, default: bool) -> bool:
    val = ctx.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "enabled", "on", "yes")
    return bool(val)


def _set_bool(ctx: dict[str, Any], key: str, value: bool) -> None:
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
