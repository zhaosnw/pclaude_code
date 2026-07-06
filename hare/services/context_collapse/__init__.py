"""Port of: src/services/contextCollapse/

Context collapse — newer alternative to compaction.
Stub — not yet implemented.
"""

from __future__ import annotations
from typing import Any


def is_context_collapse_enabled() -> bool:
    return False


def recover_from_overflow(
    messages: list[Any],
    query_source: str,
) -> dict[str, Any] | None:
    return None


def apply_collapses_if_needed(
    messages: list[Any],
    tool_use_context: Any,
    query_source: str,
) -> dict[str, Any]:
    return {"messages": messages}
