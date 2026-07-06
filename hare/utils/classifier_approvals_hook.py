"""
External-store hook for classifier checking (`classifierApprovalsHook.ts`).

Python has no React: use `subscribe_classifier_checking` + `is_classifier_checking`
like `useSyncExternalStore`.
"""

from __future__ import annotations

from hare.utils.permissions.classifier_approvals import (
    is_classifier_checking,
    subscribe_classifier_checking,
)


def use_is_classifier_checking(tool_use_id: str) -> bool:
    """Current snapshot value; subscribe via `subscribe_classifier_checking` for updates."""
    return is_classifier_checking(tool_use_id)


__all__ = [
    "is_classifier_checking",
    "subscribe_classifier_checking",
    "use_is_classifier_checking",
]
