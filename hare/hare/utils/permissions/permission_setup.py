"""Permission mode transitions and auto-mode gate (stub hooks). Port of permissionSetup.ts."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from hare.app_types.permissions import PermissionMode, ToolPermissionContext
from hare.utils.debug import log_for_debugging
from hare.utils.permissions import auto_mode_state


def is_auto_mode_gate_enabled() -> bool:
    return not auto_mode_state.is_auto_mode_circuit_broken()


def get_auto_mode_unavailable_reason() -> str:
    return "auto mode unavailable"


def transition_permission_mode(
    _from_mode: PermissionMode,
    to_mode: PermissionMode,
    ctx: ToolPermissionContext,
) -> ToolPermissionContext:
    return replace(ctx, mode=to_mode)


async def verify_auto_mode_gate_access(
    ctx: ToolPermissionContext,
    _fast_mode: bool | None = None,
) -> tuple[Callable[[ToolPermissionContext], ToolPermissionContext], str | None]:
    def ident(c: ToolPermissionContext) -> ToolPermissionContext:
        return c

    return ident, None


async def should_disable_bypass_permissions() -> bool:
    return False


def create_disabled_bypass_permissions_context(
    ctx: ToolPermissionContext,
) -> ToolPermissionContext:
    return replace(ctx, is_bypass_permissions_mode_available=False)


def parse_tool_list_from_cli(tool_list: str | None) -> list[str]:
    """Parse comma-separated tool list from CLI argument (P2 — stub)."""
    if not tool_list:
        return []
    return [t.strip() for t in tool_list.split(",") if t.strip()]


def can_cycle_to_auto(
    ctx: ToolPermissionContext, *, transcript_classifier: bool
) -> bool:
    if not transcript_classifier:
        return False
    gate = is_auto_mode_gate_enabled()
    can = bool(ctx.is_auto_mode_available) and gate
    if not can:
        log_for_debugging(
            f"[auto-mode] can_cycle_toAuto=false: ctx.is_auto_mode_available="
            f"{ctx.is_auto_mode_available} is_auto_mode_gate_enabled={gate} "
            f"reason={get_auto_mode_unavailable_reason()}"
        )
    return can
