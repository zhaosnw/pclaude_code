"""
/brief command - toggle brief-only mode.

Port of: src/commands/brief.ts

Toggles brief mode on/off with:
- GrowthBook feature gate (tengu_kairos_brief_config)
- Entitlement check (isBriefEntitled)
- userMsgOptIn sync for tool availability
- System reminder injection on toggle
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "brief"
DESCRIPTION = "Toggle brief-only mode"
ALIASES: list[str] = []

BRIEF_TOOL_NAME = "SendUserMessage"


async def call(args: str, **context: Any) -> dict[str, Any]:
    """Toggle brief-only mode.

    Returns dict with type, display_text, and optionally metaMessages.
    """
    get_app_state = context.get("get_app_state")
    set_app_state = context.get("set_app_state")
    is_brief_entitled = context.get("is_brief_entitled")
    set_user_msg_opt_in = context.get("set_user_msg_opt_in")
    get_kairos_active = context.get("get_kairos_active")
    get_feature_value = context.get("get_feature_value_cached_may_be_stale")
    log_event = context.get("log_event")

    if not get_app_state:
        return {"type": "text", "value": "Brief-only mode is not available."}

    current = get_app_state().get("isBriefOnly", False)
    new_state = not current

    # Entitlement check only gates the on-transition
    if new_state and is_brief_entitled:
        if not is_brief_entitled():
            if log_event:
                log_event(
                    "tengu_brief_mode_toggled",
                    {
                        "enabled": False,
                        "gated": True,
                        "source": "slash_command",
                    },
                )
            return {
                "type": "text",
                "value": "Brief tool is not enabled for your account",
                "display": "system",
            }

    # userMsgOptIn tracks isBriefOnly so the tool is available when brief mode is on
    if set_user_msg_opt_in:
        set_user_msg_opt_in(new_state)

    # Update app state
    if set_app_state:

        def _toggle_brief(prev: dict[str, Any]) -> dict[str, Any]:
            if prev.get("isBriefOnly") == new_state:
                return prev
            return {**prev, "isBriefOnly": new_state}

        set_app_state(_toggle_brief)

    if log_event:
        log_event(
            "tengu_brief_mode_toggled",
            {
                "enabled": new_state,
                "gated": False,
                "source": "slash_command",
            },
        )

    # Inject system reminder so the model knows about the transition
    meta_messages = None
    kairos_active = get_kairos_active() if get_kairos_active else False
    if not kairos_active:
        if new_state:
            reminder = (
                f"<system-reminder>\nBrief mode is now enabled. "
                f"Use the {BRIEF_TOOL_NAME} tool for all user-facing output "
                f"— plain text outside it is hidden from the user's view.\n</system-reminder>"
            )
        else:
            reminder = (
                f"<system-reminder>\nBrief mode is now disabled. "
                f"The {BRIEF_TOOL_NAME} tool is no longer available "
                f"— reply with plain text.\n</system-reminder>"
            )
        meta_messages = [reminder]

    display_text = (
        "Brief-only mode enabled" if new_state else "Brief-only mode disabled"
    )
    result: dict[str, Any] = {
        "type": "text",
        "value": display_text,
        "display": "system",
    }
    if meta_messages:
        result["metaMessages"] = meta_messages

    return result


def is_enabled(context: dict[str, Any] | None = None) -> bool:
    """Check if the /brief command should be available.

    Mirrors the TS isEnabled check with KAIROS/KAIROS_BRIEF feature flags
    and the growthbook config.
    """
    ctx = context or {}
    has_feature = ctx.get("has_feature")
    if has_feature:
        if has_feature("KAIROS") or has_feature("KAIROS_BRIEF"):
            get_feature_value = ctx.get("get_feature_value_cached_may_be_stale")
            if get_feature_value:
                config = get_feature_value(
                    "tengu_kairos_brief_config", {"enable_slash_command": False}
                )
                return config.get("enable_slash_command", False)
            return False
        return False
    return False


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "isEnabled": is_enabled,
        "immediate": True,
        "call": call,
    }
