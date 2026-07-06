"""
State change reactions — permission mode sync, model persistence, settings cache.

Port of: src/state/onChangeAppState.ts (171 lines)

Single choke point for CCR/SDK mode sync on AppState changes.
"""

from __future__ import annotations

from typing import Any


def on_change_app_state(
    new_state: Any,
    old_state: Any,
    *,
    notify_permission_mode_changed: Any = None,
    notify_session_metadata_changed: Any = None,
    update_settings_for_source: Any = None,
    set_main_loop_model_override: Any = None,
    save_global_config: Any = None,
    get_global_config: Any = None,
    clear_api_key_helper_cache: Any = None,
    clear_aws_credentials_cache: Any = None,
    clear_gcp_credentials_cache: Any = None,
    apply_config_environment_variables: Any = None,
) -> None:
    """React to AppState changes — syncs state to persistence layer and CCR."""

    # 1. Permission mode change → notify CCR + SDK
    new_ctx = getattr(new_state, "tool_permission_context", None) or new_state.get(
        "toolPermissionContext", {}
    )
    old_ctx = getattr(old_state, "tool_permission_context", None) or old_state.get(
        "toolPermissionContext", {}
    )
    prev_mode = (
        getattr(new_ctx, "mode", None) or new_ctx.get("mode", "") if new_ctx else ""
    )
    new_mode = (
        getattr(old_ctx, "mode", None) or old_ctx.get("mode", "")
        if old_ctx
        else prev_mode
    )

    if prev_mode != new_mode:
        new_mode_external = (
            _to_external_permission_mode(new_mode)
            if hasattr(new_mode, "lower") and new_mode
            else new_mode
        )
        prev_mode_external = (
            _to_external_permission_mode(prev_mode)
            if hasattr(prev_mode, "lower") and prev_mode
            else prev_mode
        )
        if prev_mode_external != new_mode_external and notify_session_metadata_changed:
            is_ultraplan = (
                new_mode_external == "plan"
                and getattr(new_state, "isUltraplanMode", False)
                and not getattr(old_state, "isUltraplanMode", False)
            )
            notify_session_metadata_changed(
                {
                    "permission_mode": new_mode_external,
                    "is_ultraplan_mode": True if is_ultraplan else None,
                }
            )
        if notify_permission_mode_changed:
            notify_permission_mode_changed(new_mode)

    # 2. Model change → settings persistence
    new_model = getattr(new_state, "main_loop_model", None)
    old_model = getattr(old_state, "main_loop_model", None)
    if new_model != old_model:
        if new_model is None and update_settings_for_source:
            update_settings_for_source("userSettings", {"model": None})
            if set_main_loop_model_override:
                set_main_loop_model_override(None)
        elif new_model is not None and update_settings_for_source:
            update_settings_for_source("userSettings", {"model": new_model})
            if set_main_loop_model_override:
                set_main_loop_model_override(new_model)

    # 3. expandedView → config persistence
    new_expanded = getattr(new_state, "expanded_view", None)
    old_expanded = getattr(old_state, "expanded_view", None)
    if new_expanded != old_expanded:
        show_todos = new_expanded == "tasks"
        show_teammates = new_expanded == "teammates"
        if save_global_config and get_global_config:
            cfg = get_global_config()
            if (
                cfg.get("showExpandedTodos") != show_todos
                or cfg.get("showSpinnerTree") != show_teammates
            ):
                save_global_config(
                    lambda c: {
                        **c,
                        "showExpandedTodos": show_todos,
                        "showSpinnerTree": show_teammates,
                    }
                )

    # 4. verbose → config persistence
    new_verbose = getattr(new_state, "verbose", None)
    old_verbose = getattr(old_state, "verbose", None)
    if new_verbose != old_verbose and save_global_config and get_global_config:
        if get_global_config().get("verbose") != new_verbose:
            save_global_config(lambda c: {**c, "verbose": new_verbose})

    # 5. Settings → clear auth caches + re-apply env
    new_settings = getattr(new_state, "settings", None)
    old_settings = getattr(old_state, "settings", None)
    if new_settings != old_settings:
        try:
            if clear_api_key_helper_cache:
                clear_api_key_helper_cache()
            if clear_aws_credentials_cache:
                clear_aws_credentials_cache()
            if clear_gcp_credentials_cache:
                clear_gcp_credentials_cache()
            new_env = (
                new_settings.get("env")
                if isinstance(new_settings, dict)
                else getattr(new_settings, "env", None)
            )
            old_env = (
                old_settings.get("env")
                if isinstance(old_settings, dict)
                else getattr(old_settings, "env", None)
            )
            if new_env != old_env and apply_config_environment_variables:
                apply_config_environment_variables()
        except Exception:
            pass


def _to_external_permission_mode(mode: str) -> str:
    """Convert internal permission mode names to external ones."""
    if mode in ("default", "bubble", "ungated_auto"):
        return "default"
    return mode
