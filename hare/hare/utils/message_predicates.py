"""Message type guards — port of `messagePredicates.ts`."""

from __future__ import annotations

from typing import Any, TypeGuard


def is_human_turn(m: dict[str, Any]) -> bool:
    return (
        m.get("type") == "user"
        and not m.get("isMeta")
        and m.get("toolUseResult") is None
    )


def is_human_turn_typed(m: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(m, dict) and is_human_turn(m)
