"""Bootstrap state — re-exports from state.py"""

from hare.bootstrap.state import (
    # Session lifecycle
    get_session_id,
    set_session_id,
    regenerate_session_id,
    get_parent_session_id,
    switch_session,
    on_session_switch,
    get_session_project_dir,
    # Directory state
    get_original_cwd,
    set_original_cwd,
    get_project_root,
    set_project_root,
    get_cwd,
    set_cwd,
    get_cwd_state,
    set_cwd_state,
    # Cost / duration
    get_total_cost_usd,
    get_total_api_duration,
    get_total_duration,
    get_total_tool_duration,
    add_to_total_duration_state,
    add_to_total_cost_state,
    add_to_tool_duration,
    get_total_input_tokens,
    get_total_output_tokens,
    get_total_cache_read_input_tokens,
    get_total_cache_creation_input_tokens,
    # Turn-level
    get_turn_output_tokens,
    get_current_turn_token_budget,
    snapshot_output_tokens_for_turn,
    get_budget_continuation_count,
    increment_budget_continuation_count,
    # Interaction time
    update_last_interaction_time,
    flush_interaction_time,
    get_last_interaction_time,
    # Interactive / non-interactive
    get_is_interactive,
    set_is_interactive,
    get_is_non_interactive_session,
    set_is_non_interactive_session,
    # Feature flags
    get_kairos_active,
    set_kairos_active,
    get_user_msg_opt_in,
    set_user_msg_opt_in,
    # Model
    get_model_usage,
    get_main_loop_model_override,
    set_main_loop_model_override,
    # Cost state
    reset_cost_state,
    set_cost_state_for_restore,
    reset_state_for_tests,
    # Post-compaction
    mark_post_compaction,
    consume_post_compaction,
    # Scroll drain
    mark_scroll_activity,
    get_is_scroll_draining,
    wait_for_scroll_idle,
    # Telemetry
    set_meter,
    get_meter,
    get_session_counter,
    get_active_time_counter,
    # Logger
    get_logger_provider,
    set_logger_provider,
    # Tokens & API
    get_session_ingress_token,
    set_session_ingress_token,
    get_last_api_request,
    set_last_api_request,
    get_last_main_request_id,
    set_last_main_request_id,
    # Plugins
    set_use_cowork_plugins,
    get_use_cowork_plugins,
    set_inline_plugins,
    get_inline_plugins,
    # Client
    get_client_type,
    set_client_type,
    # Persistence / trust
    is_session_persistence_disabled,
    set_session_persistence_disabled,
    get_session_trust_accepted,
    set_session_trust_accepted,
    # Plan / auto mode
    has_exited_plan_mode_in_session,
    set_has_exited_plan_mode,
    handle_plan_mode_transition,
    handle_auto_mode_transition,
    # Hooks
    register_hook_callbacks,
    get_registered_hooks,
    clear_registered_hooks,
    # Skills
    add_invoked_skill,
    get_invoked_skills,
    clear_invoked_skills,
    # Cron tasks
    add_session_cron_task,
    get_session_cron_tasks,
    remove_session_cron_tasks,
    # Beta header latches
    clear_beta_header_latches,
    get_afk_mode_header_latched,
    set_afk_mode_header_latched,
    get_fast_mode_header_latched,
    set_fast_mode_header_latched,
    # Additional directories
    get_additional_directories_for_claude_md,
    set_additional_directories_for_claude_md,
    # SDK
    get_sdk_betas,
    set_sdk_betas,
    # System prompt
    get_system_prompt_section_cache,
    clear_system_prompt_section_state,
    # Session metadata (for restore)
    set_agent_name,
    set_agent_color,
    set_agent_setting,
    set_custom_title,
    set_tag,
    set_mode,
    set_worktree_session,
)
