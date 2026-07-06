"""
Bridge permission callback types and helpers.

Port of: src/bridge/bridgePermissionCallbacks.ts
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

# BridgePermissionResponse: checked by `behavior` discriminant
# { behavior: 'allow' | 'deny', updatedInput?, updatedPermissions?, message? }


def is_bridge_permission_response(value: Any) -> bool:
    """Type predicate: validates behavior discriminant field."""
    if not value or not isinstance(value, dict):
        return False
    behavior = value.get("behavior")
    return behavior in ("allow", "deny")


class BridgePermissionCallbacks(Protocol):
    """Interface for bridge permission callback handlers."""

    def send_request(
        self,
        request_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        tool_use_id: str,
        description: str,
        permission_suggestions: Optional[list[dict[str, Any]]] = None,
        blocked_path: Optional[str] = None,
    ) -> None: ...

    def send_response(self, request_id: str, response: dict[str, Any]) -> None: ...

    def cancel_request(self, request_id: str) -> None: ...

    def on_response(
        self,
        request_id: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> Callable[[], None]: ...  # returns unsubscribe
