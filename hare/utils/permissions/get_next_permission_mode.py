"""Shift+Tab permission mode cycling. Port of getNextPermissionMode.ts."""

from __future__ import annotations

import os

from hare.app_types.permissions import PermissionMode, ToolPermissionContext
from hare.utils.permissions.permission_setup import (
    can_cycle_to_auto,
    transition_permission_mode,
)


def get_next_permission_mode(
    tool_permission_context: ToolPermissionContext,
    _team_context: dict[str, str] | None = None,
    *,
    transcript_classifier: bool = False,
) -> PermissionMode:
    ctx = tool_permission_context
    mode = ctx.mode
    user_type_ant = os.environ.get("USER_TYPE") == "ant"

    if mode == "default":
        if user_type_ant:
            if ctx.is_bypass_permissions_mode_available:
                return "bypassPermissions"
            if can_cycle_to_auto(ctx, transcript_classifier=transcript_classifier):
                return "auto"
            return "default"
        return "acceptEdits"

    if mode == "acceptEdits":
        return "plan"

    if mode == "plan":
        if ctx.is_bypass_permissions_mode_available:
            return "bypassPermissions"
        if can_cycle_to_auto(ctx, transcript_classifier=transcript_classifier):
            return "auto"
        return "default"

    if mode == "bypassPermissions":
        if can_cycle_to_auto(ctx, transcript_classifier=transcript_classifier):
            return "auto"
        return "default"

    if mode == "dontAsk":
        return "default"

    return "default"


def cycle_permission_mode(
    tool_permission_context: ToolPermissionContext,
    team_context: dict[str, str] | None = None,
    *,
    transcript_classifier: bool = False,
) -> tuple[PermissionMode, ToolPermissionContext]:
    next_mode = get_next_permission_mode(
        tool_permission_context,
        team_context,
        transcript_classifier=transcript_classifier,
    )
    new_ctx = transition_permission_mode(
        tool_permission_context.mode,
        next_mode,
        tool_permission_context,
    )
    return next_mode, new_ctx
