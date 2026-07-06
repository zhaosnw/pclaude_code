"""
Permission update destination and validation helpers.

Port of: src/utils/permissions/PermissionUpdateSchema.ts
"""

from __future__ import annotations

from typing import Literal, get_args

from hare.app_types.permissions import (
    ExternalPermissionMode,
    PermissionUpdate,
    PermissionUpdateDestination,
)

PERMISSION_UPDATE_DESTINATIONS: tuple[PermissionUpdateDestination, ...] = get_args(
    # PermissionUpdateDestination is a Union of literals in types; re-declare for runtime
    Literal[  # type: ignore[misc]
        "userSettings",
        "projectSettings",
        "localSettings",
        "session",
        "cliArg",
    ]
)


def is_permission_update_destination(value: str) -> bool:
    return value in (
        "userSettings",
        "projectSettings",
        "localSettings",
        "session",
        "cliArg",
    )


__all__ = [
    "PermissionUpdate",
    "PermissionUpdateDestination",
    "ExternalPermissionMode",
    "is_permission_update_destination",
]
