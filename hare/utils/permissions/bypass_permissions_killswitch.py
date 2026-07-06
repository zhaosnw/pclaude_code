"""One-shot bypass-permissions and auto-mode gate checks. Port of bypassPermissionsKillswitch.ts."""

from __future__ import annotations

from typing import Any, Callable

from hare.app_types.permissions import ToolPermissionContext
from hare.utils.permissions.permission_setup import (
    create_disabled_bypass_permissions_context,
    should_disable_bypass_permissions,
    verify_auto_mode_gate_access,
)

_bypass_ran = False
_auto_ran = False


async def check_and_disable_bypass_permissions_if_needed(
    tool_permission_context: ToolPermissionContext,
    set_app_state: Callable[[Any], Any],
) -> None:
    global _bypass_ran
    if _bypass_ran:
        return
    _bypass_ran = True
    if not tool_permission_context.is_bypass_permissions_mode_available:
        return
    if not await should_disable_bypass_permissions():
        return

    def updater(prev: Any) -> Any:
        tpc = create_disabled_bypass_permissions_context(prev.tool_permission_context)
        return {**prev, "tool_permission_context": tpc}

    set_app_state(updater)


def reset_bypass_permissions_check() -> None:
    global _bypass_ran
    _bypass_ran = False


async def check_and_disable_auto_mode_if_needed(
    tool_permission_context: ToolPermissionContext,
    set_app_state: Callable[[Any], Any],
    fast_mode: bool | None = None,
) -> None:
    global _auto_ran
    if _auto_ran:
        return
    _auto_ran = True
    update_ctx, _notification = await verify_auto_mode_gate_access(
        tool_permission_context, fast_mode
    )

    def updater(prev: Any) -> Any:
        next_ctx = update_ctx(prev.tool_permission_context)
        if next_ctx is prev.tool_permission_context:
            return prev
        return {**prev, "tool_permission_context": next_ctx}

    set_app_state(updater)


def reset_auto_mode_gate_check() -> None:
    global _auto_ran
    _auto_ran = False
