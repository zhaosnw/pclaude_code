"""
Permission result helpers.

Port of: src/utils/permissions/PermissionResult.ts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hare.app_types.permissions import PermissionResult

if TYPE_CHECKING:
    pass


def get_rule_behavior_description(permission_result: PermissionResult) -> str:
    """Prose description for analytics / UI copy."""
    b = getattr(permission_result, "behavior", None)
    if b == "allow":
        return "allowed"
    if b == "deny":
        return "denied"
    return "asked for confirmation for"
