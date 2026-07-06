"""Debug log category parsing and filtering (`debugFilter.ts`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class DebugFilter:
    include: list[str]
    exclude: list[str]
    is_exclusive: bool


@lru_cache(maxsize=32)
def parse_debug_filter(filter_string: str | None) -> DebugFilter | None:
    if not filter_string or not filter_string.strip():
        return None
    filters = [f.strip() for f in filter_string.split(",") if f.strip()]
    if not filters:
        return None
    has_exclusive = any(f.startswith("!") for f in filters)
    has_inclusive = any(not f.startswith("!") for f in filters)
    if has_exclusive and has_inclusive:
        return None
    clean = [f.removeprefix("!").lower() for f in filters]
    return DebugFilter(
        include=[] if has_exclusive else clean,
        exclude=clean if has_exclusive else [],
        is_exclusive=has_exclusive,
    )


def extract_debug_categories(message: str) -> list[str]:
    categories: list[str] = []

    mcp_match = re.match(r'^MCP server ["\']([^"\']+)["\']', message)
    if mcp_match:
        categories.extend(["mcp", mcp_match.group(1).lower()])
    else:
        prefix_match = re.match(r"^([^:\[]+):", message)
        if prefix_match:
            categories.append(prefix_match.group(1).strip().lower())

    bracket_match = re.match(r"^\[([^\]]+)]", message)
    if bracket_match:
        categories.append(bracket_match.group(1).strip().lower())

    if "1p event:" in message.lower():
        categories.append("1p")

    secondary_match = re.search(
        r":\s*([^:]+?)(?:\s+(?:type|mode|status|event))?:", message
    )
    if secondary_match:
        secondary = secondary_match.group(1).strip().lower()
        if len(secondary) < 30 and " " not in secondary:
            categories.append(secondary)

    return list(dict.fromkeys(categories))


def should_show_debug_categories(
    categories: list[str],
    filter_cfg: DebugFilter | None,
) -> bool:
    if filter_cfg is None:
        return True
    if not categories:
        return False
    if filter_cfg.is_exclusive:
        return not any(cat in filter_cfg.exclude for cat in categories)
    return any(cat in filter_cfg.include for cat in categories)


def should_show_debug_message(message: str, filter_cfg: DebugFilter | None) -> bool:
    if filter_cfg is None:
        return True
    return should_show_debug_categories(extract_debug_categories(message), filter_cfg)
