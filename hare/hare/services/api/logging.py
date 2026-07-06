"""
API logging and usage types.

Port of: src/services/api/logging.ts, emptyUsage.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NonNullableUsage:
    """Non-nullable usage tracking."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    server_tool_use_input_tokens: int = 0


def empty_usage() -> NonNullableUsage:
    """Create an empty usage object."""
    return NonNullableUsage()


def _usage_value(source: Any, key: str) -> int:
    """Read a usage field from either a usage object or a plain dict."""
    if source is None:
        return 0
    if isinstance(source, dict):
        value = source.get(key, 0)
    else:
        value = getattr(source, key, 0)
    return value if isinstance(value, int) else 0


def accumulate_usage(
    target: NonNullableUsage,
    source: Any,
) -> NonNullableUsage:
    """Accumulate usage from source into target."""
    target.input_tokens += _usage_value(source, "input_tokens")
    target.output_tokens += _usage_value(source, "output_tokens")
    target.cache_creation_input_tokens += _usage_value(
        source, "cache_creation_input_tokens"
    )
    target.cache_read_input_tokens += _usage_value(source, "cache_read_input_tokens")
    target.server_tool_use_input_tokens += _usage_value(
        source, "server_tool_use_input_tokens"
    )
    return target


def update_usage(
    target: NonNullableUsage,
    source: Any,
) -> NonNullableUsage:
    """Update usage (alias for accumulate_usage for compatibility)."""
    return accumulate_usage(target, source)


EMPTY_USAGE = NonNullableUsage()
