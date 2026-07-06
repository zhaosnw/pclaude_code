"""Merge and filter tool pools (port of toolPool.ts)."""

from __future__ import annotations

from typing import Any

try:
    from hare.constants.tools import COORDINATOR_MODE_ALLOWED_TOOLS
except ImportError:
    COORDINATOR_MODE_ALLOWED_TOOLS = frozenset()  # type: ignore[misc]

PR_ACTIVITY_TOOL_SUFFIXES = ("subscribe_pr_activity", "unsubscribe_pr_activity")


def is_pr_activity_subscription_tool(name: str) -> bool:
    return any(name.endswith(s) for s in PR_ACTIVITY_TOOL_SUFFIXES)


def apply_coordinator_tool_filter(tools: list[Any]) -> list[Any]:
    if not COORDINATOR_MODE_ALLOWED_TOOLS:
        return list(tools)
    return [
        t
        for t in tools
        if getattr(t, "name", None) in COORDINATOR_MODE_ALLOWED_TOOLS
        or is_pr_activity_subscription_tool(getattr(t, "name", ""))
    ]


def merge_and_filter_tools(
    initial_tools: list[Any],
    assembled: list[Any],
    _mode: str,
) -> list[Any]:
    merged = _uniq_by_name([*initial_tools, *assembled])
    mcp = [t for t in merged if _is_mcp_tool(t)]
    built_in = [t for t in merged if not _is_mcp_tool(t)]
    tools = sorted(built_in, key=lambda x: getattr(x, "name", "")) + sorted(
        mcp, key=lambda x: getattr(x, "name", "")
    )
    try:
        from hare.coordinator import coordinator_mode as cm

        if cm.is_coordinator_mode():
            return apply_coordinator_tool_filter(tools)
    except ImportError:
        pass
    return tools


def _uniq_by_name(tools: list[Any]) -> list[Any]:
    seen: dict[str, Any] = {}
    for t in tools:
        n = getattr(t, "name", None)
        if n:
            seen[str(n)] = t
    return list(seen.values())


def _is_mcp_tool(tool: Any) -> bool:
    try:
        from hare.services.mcp.utils import is_mcp_tool

        return bool(is_mcp_tool(tool))
    except ImportError:
        return "mcp" in getattr(tool, "name", "").lower()
