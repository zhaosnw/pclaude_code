"""Port of: src/commands/rate-limit-options/. Rate limit tiers and status."""

from __future__ import annotations
from typing import Any

COMMAND_NAME = "rate-limit-options"
DESCRIPTION = "Show current rate limit status and tier information"
ALIASES: list[str] = ["rate-limit"]


async def call(args: list[str], context: Any) -> dict[str, Any]:
    ctx = context if isinstance(context, dict) else {}
    rate_info = ctx.get("rate_limit", ctx.get("rate_limit_info"))
    current_tier = _extract_tier(rate_info)

    lines = ["# Rate Limit Status\n"]
    if current_tier:
        lines.append(f"**Current tier**: {current_tier}")
    else:
        lines.append("**Current tier**: Standard (default)")

    lines.extend(["",
        "## Tiers",
        "- **Free**: 10 requests/min, basic models",
        "- **Standard**: 30 requests/min, all models",
        "- **Pro**: 50 requests/min, priority queue",
        "- **Max**: 100 requests/min, highest priority",
        "",
        "Manage: https://console.anthropic.com/settings/limits",
        "Use `/effort` to control reasoning depth.",
    ])
    return {"type": "text", "value": "\n".join(lines)}


def _extract_tier(info: Any) -> str:
    if info is None:
        return ""
    if isinstance(info, str):
        return info
    if isinstance(info, dict):
        return str(info.get("tier", info.get("name", "")))
    return str(getattr(info, "tier", ""))
