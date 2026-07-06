"""
Global bootstrap state — session ID, project root, cost tracking, flags,
telemetry counters, beta header latches, and more.

Port of: src/bootstrap/state.ts (1759 lines, ~215 fields, ~130 functions)

DO NOT ADD MORE STATE HERE — BE JUDICIOUS WITH GLOBAL STATE.
"""

from __future__ import annotations

import os
import time
import unicodedata
from typing import Any, Callable, Optional, Union
from uuid import uuid4


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_IN_MEMORY_ERRORS = 100
MAX_SLOW_OPERATIONS = 10
SLOW_OPERATION_TTL_MS = 10000
SCROLL_DRAIN_IDLE_MS = 150


# ===========================================================================
# STATE OBJECT — single global namespace matching TS State type
# ===========================================================================


class _BootstrapState:
    """Single global state container. Match TS State type field-for-field."""

    def __init__(self) -> None:
        # Resolve cwd with NFC normalization (matching TS realpathSync + normalize('NFC'))
        resolved_cwd = ""
        try:
            raw = os.getcwd()
            try:
                resolved_cwd = unicodedata.normalize("NFC", os.path.realpath(raw))
            except OSError:
                resolved_cwd = unicodedata.normalize("NFC", raw)
        except Exception:
            resolved_cwd = os.getcwd()

        self.original_cwd: str = resolved_cwd
        self.project_root: str = resolved_cwd
        self.total_cost_usd: float = 0.0
        self.total_api_duration: float = 0.0
        self.total_api_duration_without_retries: float = 0.0
        self.total_tool_duration: float = 0.0
        self.turn_hook_duration_ms: float = 0.0
        self.turn_tool_duration_ms: float = 0.0
        self.turn_classifier_duration_ms: float = 0.0
        self.turn_tool_count: int = 0
        self.turn_hook_count: int = 0
        self.turn_classifier_count: int = 0
        self.start_time: float = time.time() * 1000
        self.last_interaction_time: float = time.time() * 1000
        self.total_lines_added: int = 0
        self.total_lines_removed: int = 0
        self.has_unknown_model_cost: bool = False
        self.cwd: str = resolved_cwd
        self.model_usage: dict[str, Any] = {}
        self.main_loop_model_override: Any = None
        self.initial_main_loop_model: Any = None
        self.model_strings: Any = None
        self.is_interactive: bool = False
        self.kairos_active: bool = False
        self.strict_tool_result_pairing: bool = False
        self.sdk_agent_progress_summaries_enabled: bool = False
        self.user_msg_opt_in: bool = False
        self.client_type: str = "cli"
        self.session_source: Optional[str] = None
        self.question_preview_format: Optional[str] = None  # 'markdown' | 'html'
        self.flag_settings_path: Optional[str] = None
        self.flag_settings_inline: Optional[dict[str, Any]] = None
        self.allowed_setting_sources: list[str] = [
            "userSettings",
            "projectSettings",
            "localSettings",
            "flagSettings",
            "policySettings",
        ]
        self.session_ingress_token: Optional[str] = None
        self.oauth_token_from_fd: Optional[str] = None
        self.api_key_from_fd: Optional[str] = None
        # Telemetry
        self.meter: Any = None
        self.session_counter: Any = None
        self.loc_counter: Any = None
        self.pr_counter: Any = None
        self.commit_counter: Any = None
        self.cost_counter: Any = None
        self.token_counter: Any = None
        self.code_edit_tool_decision_counter: Any = None
        self.active_time_counter: Any = None
        self.stats_store: Any = None
        self.session_id: str = str(uuid4())
        self.parent_session_id: Optional[str] = None
        # Logger state
        self.logger_provider: Any = None
        self.event_logger: Any = None
        self.meter_provider: Any = None
        self.tracer_provider: Any = None
        # Agent color
        self.agent_color_map: dict[str, str] = {}
        self.agent_color_index: int = 0
        # Last API request
        self.last_api_request: Any = None
        self.last_api_request_messages: Any = None
        self.last_classifier_requests: Any = None
        self.cached_claude_md_content: Optional[str] = None
        self.in_memory_error_log: list[dict[str, str]] = []
        self.inline_plugins: list[str] = []
        self.chrome_flag_override: Optional[bool] = None
        self.use_cowork_plugins: bool = False
        self.session_bypass_permissions_mode: bool = False
        self.scheduled_tasks_enabled: bool = False
        self.session_cron_tasks: list[dict[str, Any]] = []
        self.session_created_teams: set[str] = set()
        self.session_trust_accepted: bool = False
        self.session_persistence_disabled: bool = False
        self.has_exited_plan_mode: bool = False
        self.needs_plan_mode_exit_attachment: bool = False
        self.needs_auto_mode_exit_attachment: bool = False
        self.lsp_recommendation_shown_this_session: bool = False
        self.init_json_schema: Optional[dict[str, Any]] = None
        self.registered_hooks: Optional[dict[str, list[Any]]] = None
        self.plan_slug_cache: dict[str, str] = {}
        self.teleported_session_info: Optional[dict[str, Any]] = None
        self.invoked_skills: dict[str, dict[str, Any]] = {}
        self.slow_operations: list[dict[str, Any]] = []
        self.sdk_betas: Optional[list[str]] = None
        self.main_thread_agent_type: Optional[str] = None
        self.is_remote_mode: bool = False
        self.direct_connect_server_url: Optional[str] = None
        self.system_prompt_section_cache: dict[str, Optional[str]] = {}
        self.last_emitted_date: Optional[str] = None
        self.additional_directories_for_claude_md: list[str] = []
        self.allowed_channels: list[dict[str, Any]] = []
        self.has_dev_channels: bool = False
        self.session_project_dir: Optional[str] = None
        self.prompt_cache_1h_allowlist: Optional[list[str]] = None
        self.prompt_cache_1h_eligible: Optional[bool] = None
        self.afk_mode_header_latched: Optional[bool] = None
        self.fast_mode_header_latched: Optional[bool] = None
        self.cache_editing_header_latched: Optional[bool] = None
        self.thinking_clear_latched: Optional[bool] = None
        self.prompt_id: Optional[str] = None
        # Session metadata fields (restored from session JSONL metadata)
        self._agent_name: Optional[str] = None
        self._agent_color: Optional[str] = None
        self._agent_setting: Any = None
        self._custom_title: Optional[str] = None
        self._tag: Optional[str] = None
        self._mode: Optional[str] = None
        self._worktree_session: Any = None
        self.last_main_request_id: Optional[str] = None
        self.last_api_completion_timestamp: Optional[float] = None
        self.pending_post_compaction: bool = False
        # ant-only
        self.repl_bridge_active: bool = False


# Global singleton
_S = _BootstrapState()

# Token budget module-scope (not in S since they're turn-scoped)
_output_tokens_at_turn_start: int = 0
_current_turn_token_budget: Optional[int] = None
_budget_continuation_count: int = 0

# Scroll drain module-scope (ephemeral hot-path, not in STATE)
_scroll_draining: bool = False
_scroll_drain_timer: Any = None

# Interaction time dirty flag
_interaction_time_dirty: bool = False

# Session switch signal
_session_switch_callbacks: list[Callable[[str], None]] = []


# ===========================================================================
# Session ID & lifecycle
# ===========================================================================


def get_session_id() -> str:
    return _S.session_id


def regenerate_session_id(set_current_as_parent: bool = False) -> str:
    if set_current_as_parent:
        _S.parent_session_id = _S.session_id
    _S.plan_slug_cache.pop(_S.session_id, None)
    _S.session_id = str(uuid4())
    _S.session_project_dir = None
    return _S.session_id


def get_parent_session_id() -> Optional[str]:
    return _S.parent_session_id


def switch_session(session_id: str, project_dir: Optional[str] = None) -> None:
    """Atomically switch active session. project_dir=null derives from originalCwd."""
    _S.plan_slug_cache.pop(_S.session_id, None)
    _S.session_id = session_id
    _S.session_project_dir = project_dir
    for fn in _session_switch_callbacks:
        fn(session_id)


def set_session_id(new_id: str) -> None:
    """Direct setter — convenience for consumers that don't need projectDir."""
    switch_session(new_id)


def on_session_switch(cb: Callable[[str], None]) -> None:
    _session_switch_callbacks.append(cb)


def get_session_project_dir() -> Optional[str]:
    return _S.session_project_dir


# ===========================================================================
# Directory & CWD state
# ===========================================================================


def get_original_cwd() -> str:
    return _S.original_cwd


def get_project_root() -> str:
    return _S.project_root


def set_original_cwd(cwd: str) -> None:
    _S.original_cwd = unicodedata.normalize("NFC", cwd)


def set_project_root(cwd: str) -> None:
    _S.project_root = unicodedata.normalize("NFC", cwd)


def get_cwd() -> str:
    return _S.cwd


def set_cwd(path: str) -> None:
    _S.cwd = unicodedata.normalize("NFC", path)


def get_cwd_state() -> str:
    return _S.cwd


def set_cwd_state(cwd: str) -> None:
    _S.cwd = unicodedata.normalize("NFC", cwd)


def get_direct_connect_server_url() -> Optional[str]:
    return _S.direct_connect_server_url


def set_direct_connect_server_url(url: str) -> None:
    _S.direct_connect_server_url = url


# ===========================================================================
# Cost & duration tracking
# ===========================================================================


def _sum_model_usage_field(field: str) -> float:
    """Sum a field across all modelUsage entries."""
    return sum(u.get(field, 0) for u in _S.model_usage.values())


def add_to_total_duration_state(
    duration: float, duration_without_retries: float
) -> None:
    _S.total_api_duration += duration
    _S.total_api_duration_without_retries += duration_without_retries


def reset_total_duration_state_and_cost_FOR_TESTS_ONLY() -> None:
    _S.total_api_duration = 0.0
    _S.total_api_duration_without_retries = 0.0
    _S.total_cost_usd = 0.0


def add_to_total_cost_state(cost: float, model_usage: Any, model: str) -> None:
    _S.model_usage[model] = model_usage
    _S.total_cost_usd += cost


def get_total_cost_usd() -> float:
    return _S.total_cost_usd


def get_total_api_duration() -> float:
    return _S.total_api_duration


def get_total_duration() -> float:
    return time.time() * 1000 - _S.start_time


def get_total_api_duration_without_retries() -> float:
    return _S.total_api_duration_without_retries


def get_total_tool_duration() -> float:
    return _S.total_tool_duration


def add_to_tool_duration(duration: float) -> None:
    _S.total_tool_duration += duration
    _S.turn_tool_duration_ms += duration
    _S.turn_tool_count += 1


def get_turn_hook_duration_ms() -> float:
    return _S.turn_hook_duration_ms


def add_to_turn_hook_duration(duration: float) -> None:
    _S.turn_hook_duration_ms += duration
    _S.turn_hook_count += 1


def reset_turn_hook_duration() -> None:
    _S.turn_hook_duration_ms = 0.0
    _S.turn_hook_count = 0


def get_turn_hook_count() -> int:
    return _S.turn_hook_count


def get_turn_tool_duration_ms() -> float:
    return _S.turn_tool_duration_ms


def reset_turn_tool_duration() -> None:
    _S.turn_tool_duration_ms = 0.0
    _S.turn_tool_count = 0


def get_turn_tool_count() -> int:
    return _S.turn_tool_count


def get_turn_classifier_duration_ms() -> float:
    return _S.turn_classifier_duration_ms


def add_to_turn_classifier_duration(duration: float) -> None:
    _S.turn_classifier_duration_ms += duration
    _S.turn_classifier_count += 1


def reset_turn_classifier_duration() -> None:
    _S.turn_classifier_duration_ms = 0.0
    _S.turn_classifier_count = 0


def get_turn_classifier_count() -> int:
    return _S.turn_classifier_count


# ===========================================================================
# Stats store
# ===========================================================================


def get_stats_store() -> Any:
    return _S.stats_store


def set_stats_store(store: Any) -> None:
    _S.stats_store = store


# ===========================================================================
# Interaction time
# ===========================================================================


def update_last_interaction_time(immediate: bool = False) -> None:
    global _interaction_time_dirty
    if immediate:
        _S.last_interaction_time = time.time() * 1000
        _interaction_time_dirty = False
    else:
        _interaction_time_dirty = True


def flush_interaction_time() -> None:
    global _interaction_time_dirty
    if _interaction_time_dirty:
        _S.last_interaction_time = time.time() * 1000
        _interaction_time_dirty = False


def get_last_interaction_time() -> float:
    return _S.last_interaction_time


# ===========================================================================
# Lines changed
# ===========================================================================


def add_to_total_lines_changed(added: int, removed: int) -> None:
    _S.total_lines_added += added
    _S.total_lines_removed += removed


def get_total_lines_added() -> int:
    return _S.total_lines_added


def get_total_lines_removed() -> int:
    return _S.total_lines_removed


# ===========================================================================
# Token totals from modelUsage
# ===========================================================================


def get_total_input_tokens() -> int:
    return int(_sum_model_usage_field("inputTokens"))


def get_total_output_tokens() -> int:
    return int(_sum_model_usage_field("outputTokens"))


def get_total_cache_read_input_tokens() -> int:
    return int(_sum_model_usage_field("cacheReadInputTokens"))


def get_total_cache_creation_input_tokens() -> int:
    return int(_sum_model_usage_field("cacheCreationInputTokens"))


def get_total_web_search_requests() -> int:
    return int(_sum_model_usage_field("webSearchRequests"))


# ===========================================================================
# Turn token budget
# ===========================================================================


def get_turn_output_tokens() -> int:
    return get_total_output_tokens() - _output_tokens_at_turn_start


def get_current_turn_token_budget() -> Optional[int]:
    return _current_turn_token_budget


def snapshot_output_tokens_for_turn(budget: Optional[int]) -> None:
    global \
        _output_tokens_at_turn_start, \
        _current_turn_token_budget, \
        _budget_continuation_count
    _output_tokens_at_turn_start = get_total_output_tokens()
    _current_turn_token_budget = budget
    _budget_continuation_count = 0


def get_budget_continuation_count() -> int:
    return _budget_continuation_count


def increment_budget_continuation_count() -> None:
    global _budget_continuation_count
    _budget_continuation_count += 1


# ===========================================================================
# Unknown model cost
# ===========================================================================


def set_has_unknown_model_cost() -> None:
    _S.has_unknown_model_cost = True


def has_unknown_model_cost() -> bool:
    return _S.has_unknown_model_cost


# ===========================================================================
# Last API request
# ===========================================================================


def get_last_main_request_id() -> Optional[str]:
    return _S.last_main_request_id


def set_last_main_request_id(request_id: str) -> None:
    _S.last_main_request_id = request_id


def get_last_api_completion_timestamp() -> Optional[float]:
    return _S.last_api_completion_timestamp


def set_last_api_completion_timestamp(timestamp: float) -> None:
    _S.last_api_completion_timestamp = timestamp


# ===========================================================================
# Post-compaction flag
# ===========================================================================


def mark_post_compaction() -> None:
    _S.pending_post_compaction = True


def consume_post_compaction() -> bool:
    was = _S.pending_post_compaction
    _S.pending_post_compaction = False
    return was


# ===========================================================================
# Scroll drain
# ===========================================================================


def mark_scroll_activity() -> None:
    global _scroll_draining
    _scroll_draining = True
    # Reset debounce: after 150ms idle, clear the flag
    import threading

    global _scroll_drain_timer
    if _scroll_drain_timer:
        _scroll_drain_timer.cancel()
    _scroll_drain_timer = threading.Timer(
        SCROLL_DRAIN_IDLE_MS / 1000.0, _clear_scroll_drain
    )
    _scroll_drain_timer.daemon = True
    _scroll_drain_timer.start()


def _clear_scroll_drain() -> None:
    global _scroll_draining
    _scroll_draining = False


def get_is_scroll_draining() -> bool:
    return _scroll_draining


async def wait_for_scroll_idle() -> None:
    import asyncio

    while _scroll_draining:
        await asyncio.sleep(SCROLL_DRAIN_IDLE_MS / 1000.0)


# ===========================================================================
# Model usage & model settings
# ===========================================================================


def get_model_usage() -> dict[str, Any]:
    return _S.model_usage


def get_usage_for_model(model: str) -> Any:
    return _S.model_usage.get(model)


def get_main_loop_model_override() -> Any:
    return _S.main_loop_model_override


def get_initial_main_loop_model() -> Any:
    return _S.initial_main_loop_model


def set_main_loop_model_override(model: Any) -> None:
    _S.main_loop_model_override = model


def set_initial_main_loop_model(model: Any) -> None:
    _S.initial_main_loop_model = model


def get_model_strings() -> Any:
    return _S.model_strings


def set_model_strings(model_strings: Any) -> None:
    _S.model_strings = model_strings


def reset_model_strings_for_testing_only() -> None:
    _S.model_strings = None


# ===========================================================================
# SDK betas
# ===========================================================================


def get_sdk_betas() -> Optional[list[str]]:
    return _S.sdk_betas


def set_sdk_betas(betas: Optional[list[str]]) -> None:
    _S.sdk_betas = betas


# ===========================================================================
# Cost state reset / restore
# ===========================================================================


def reset_cost_state() -> None:
    _S.total_cost_usd = 0.0
    _S.total_api_duration = 0.0
    _S.total_api_duration_without_retries = 0.0
    _S.total_tool_duration = 0.0
    _S.start_time = time.time() * 1000
    _S.total_lines_added = 0
    _S.total_lines_removed = 0
    _S.has_unknown_model_cost = False
    _S.model_usage = {}
    _S.prompt_id = None


def set_cost_state_for_restore(
    total_cost_usd: float = 0.0,
    total_api_duration: float = 0.0,
    total_api_duration_without_retries: float = 0.0,
    total_tool_duration: float = 0.0,
    total_lines_added: int = 0,
    total_lines_removed: int = 0,
    last_duration: Optional[float] = None,
    model_usage: Optional[dict[str, Any]] = None,
) -> None:
    _S.total_cost_usd = total_cost_usd
    _S.total_api_duration = total_api_duration
    _S.total_api_duration_without_retries = total_api_duration_without_retries
    _S.total_tool_duration = total_tool_duration
    _S.total_lines_added = total_lines_added
    _S.total_lines_removed = total_lines_removed
    if model_usage:
        _S.model_usage = model_usage
    if last_duration:
        _S.start_time = time.time() * 1000 - last_duration


def reset_state_for_tests() -> None:
    fresh = _BootstrapState()
    for key in fresh.__dict__:
        setattr(_S, key, getattr(fresh, key))
    global \
        _output_tokens_at_turn_start, \
        _current_turn_token_budget, \
        _budget_continuation_count
    _output_tokens_at_turn_start = 0
    _current_turn_token_budget = None
    _budget_continuation_count = 0
    global _session_switch_callbacks
    _session_switch_callbacks.clear()


# ===========================================================================
# Telemetry — meter & counters
# ===========================================================================


def set_meter(meter: Any, create_counter: Any = None) -> None:
    _S.meter = meter
    if create_counter:
        _S.session_counter = create_counter(
            "claude_code.session.count",
            {"description": "Count of CLI sessions started"},
        )
        _S.loc_counter = create_counter(
            "claude_code.lines_of_code.count",
            {"description": "Count of lines of code modified"},
        )
        _S.pr_counter = create_counter(
            "claude_code.pull_request.count",
            {"description": "Number of pull requests created"},
        )
        _S.commit_counter = create_counter(
            "claude_code.commit.count", {"description": "Number of git commits created"}
        )
        _S.cost_counter = create_counter(
            "claude_code.cost.usage",
            {"description": "Cost of the session", "unit": "USD"},
        )
        _S.token_counter = create_counter(
            "claude_code.token.usage",
            {"description": "Number of tokens used", "unit": "tokens"},
        )
        _S.code_edit_tool_decision_counter = create_counter(
            "claude_code.code_edit_tool.decision",
            {"description": "Count of code editing tool permission decisions"},
        )
        _S.active_time_counter = create_counter(
            "claude_code.active_time.total",
            {"description": "Total active time in seconds", "unit": "s"},
        )


def get_meter() -> Any:
    return _S.meter


def get_session_counter() -> Any:
    return _S.session_counter


def get_loc_counter() -> Any:
    return _S.loc_counter


def get_pr_counter() -> Any:
    return _S.pr_counter


def get_commit_counter() -> Any:
    return _S.commit_counter


def get_cost_counter() -> Any:
    return _S.cost_counter


def get_token_counter() -> Any:
    return _S.token_counter


def get_code_edit_tool_decision_counter() -> Any:
    return _S.code_edit_tool_decision_counter


def get_active_time_counter() -> Any:
    return _S.active_time_counter


# ===========================================================================
# Logger / tracer providers
# ===========================================================================


def get_logger_provider() -> Any:
    return _S.logger_provider


def set_logger_provider(provider: Any) -> None:
    _S.logger_provider = provider


def get_event_logger() -> Any:
    return _S.event_logger


def set_event_logger(logger: Any) -> None:
    _S.event_logger = logger


def get_meter_provider() -> Any:
    return _S.meter_provider


def set_meter_provider(provider: Any) -> None:
    _S.meter_provider = provider


def get_tracer_provider() -> Any:
    return _S.tracer_provider


def set_tracer_provider(provider: Any) -> None:
    _S.tracer_provider = provider


# ===========================================================================
# Interactive / non-interactive
# ===========================================================================


def get_is_non_interactive_session() -> bool:
    return not _S.is_interactive


def get_is_interactive() -> bool:
    return _S.is_interactive


def set_is_interactive(value: bool) -> None:
    _S.is_interactive = value


def set_is_non_interactive_session(value: bool) -> None:
    _S.is_interactive = not value


# ===========================================================================
# Client type
# ===========================================================================


def get_client_type() -> str:
    return _S.client_type


def set_client_type(client_type: str) -> None:
    _S.client_type = client_type


# ===========================================================================
# Feature flags
# ===========================================================================


def get_sdk_agent_progress_summaries_enabled() -> bool:
    return _S.sdk_agent_progress_summaries_enabled


def set_sdk_agent_progress_summaries_enabled(value: bool) -> None:
    _S.sdk_agent_progress_summaries_enabled = value


def get_kairos_active() -> bool:
    return _S.kairos_active


def set_kairos_active(value: bool) -> None:
    _S.kairos_active = value


def get_strict_tool_result_pairing() -> bool:
    return _S.strict_tool_result_pairing


def set_strict_tool_result_pairing(value: bool) -> None:
    _S.strict_tool_result_pairing = value


def get_user_msg_opt_in() -> bool:
    return _S.user_msg_opt_in


def set_user_msg_opt_in(value: bool) -> None:
    _S.user_msg_opt_in = value


# ===========================================================================
# Session source & question preview
# ===========================================================================


def get_session_source() -> Optional[str]:
    return _S.session_source


def set_session_source(source: str) -> None:
    _S.session_source = source


def get_question_preview_format() -> Optional[str]:
    return _S.question_preview_format


def set_question_preview_format(fmt: str) -> None:
    _S.question_preview_format = fmt


# ===========================================================================
# Agent color
# ===========================================================================


def get_agent_color_map() -> dict[str, str]:
    return _S.agent_color_map


# ===========================================================================
# Flag settings
# ===========================================================================


def get_flag_settings_path() -> Optional[str]:
    return _S.flag_settings_path


def set_flag_settings_path(path: Optional[str]) -> None:
    _S.flag_settings_path = path


def get_flag_settings_inline() -> Optional[dict[str, Any]]:
    return _S.flag_settings_inline


def set_flag_settings_inline(settings: Optional[dict[str, Any]]) -> None:
    _S.flag_settings_inline = settings


# ===========================================================================
# Session tokens
# ===========================================================================


def get_session_ingress_token() -> Optional[str]:
    return _S.session_ingress_token


def set_session_ingress_token(token: Optional[str]) -> None:
    _S.session_ingress_token = token


def get_oauth_token_from_fd() -> Optional[str]:
    return _S.oauth_token_from_fd


def set_oauth_token_from_fd(token: Optional[str]) -> None:
    _S.oauth_token_from_fd = token


def get_api_key_from_fd() -> Optional[str]:
    return _S.api_key_from_fd


def set_api_key_from_fd(key: Optional[str]) -> None:
    _S.api_key_from_fd = key


# ===========================================================================
# Last API request (for /share, bug reports)
# ===========================================================================


def set_last_api_request(params: Any) -> None:
    _S.last_api_request = params


def get_last_api_request() -> Any:
    return _S.last_api_request


def set_last_api_request_messages(messages: Any) -> None:
    _S.last_api_request_messages = messages


def get_last_api_request_messages() -> Any:
    return _S.last_api_request_messages


def set_last_classifier_requests(requests: Any) -> None:
    _S.last_classifier_requests = requests


def get_last_classifier_requests() -> Any:
    return _S.last_classifier_requests


# ===========================================================================
# Cached CLAUDE.md
# ===========================================================================


def set_cached_claude_md_content(content: Optional[str]) -> None:
    _S.cached_claude_md_content = content


def get_cached_claude_md_content() -> Optional[str]:
    return _S.cached_claude_md_content


# ===========================================================================
# In-memory error log
# ===========================================================================


def add_to_in_memory_error_log(error_info: dict[str, str]) -> None:
    if len(_S.in_memory_error_log) >= MAX_IN_MEMORY_ERRORS:
        _S.in_memory_error_log.pop(0)
    _S.in_memory_error_log.append(error_info)


# ===========================================================================
# Allowed setting sources
# ===========================================================================


def get_allowed_setting_sources() -> list[str]:
    return _S.allowed_setting_sources


def set_allowed_setting_sources(sources: list[str]) -> None:
    _S.allowed_setting_sources = sources


def prefer_third_party_authentication() -> bool:
    return get_is_non_interactive_session() and _S.client_type != "claude-vscode"


# ===========================================================================
# Inline plugins
# ===========================================================================


def set_inline_plugins(plugins: list[str]) -> None:
    _S.inline_plugins = plugins


def get_inline_plugins() -> list[str]:
    return _S.inline_plugins


# ===========================================================================
# Chrome flag override
# ===========================================================================


def set_chrome_flag_override(value: Optional[bool]) -> None:
    _S.chrome_flag_override = value


def get_chrome_flag_override() -> Optional[bool]:
    return _S.chrome_flag_override


# ===========================================================================
# Cowork plugins
# ===========================================================================


def set_use_cowork_plugins(value: bool) -> None:
    _S.use_cowork_plugins = value
    # resetSettingsCache() equivalent — clear any cached settings
    try:
        from hare.utils.settings.settings_cache import reset_settings_cache

        reset_settings_cache()
    except ImportError:
        pass


def get_use_cowork_plugins() -> bool:
    return _S.use_cowork_plugins


# ===========================================================================
# Bypass permissions mode
# ===========================================================================


def set_session_bypass_permissions_mode(enabled: bool) -> None:
    _S.session_bypass_permissions_mode = enabled


def get_session_bypass_permissions_mode() -> bool:
    return _S.session_bypass_permissions_mode


# ===========================================================================
# Scheduled tasks
# ===========================================================================


def set_scheduled_tasks_enabled(enabled: bool) -> None:
    _S.scheduled_tasks_enabled = enabled


def get_scheduled_tasks_enabled() -> bool:
    return _S.scheduled_tasks_enabled


def get_session_cron_tasks() -> list[dict[str, Any]]:
    return _S.session_cron_tasks


def add_session_cron_task(task: dict[str, Any]) -> None:
    _S.session_cron_tasks.append(task)


def remove_session_cron_tasks(ids: list[str]) -> int:
    if not ids:
        return 0
    id_set = set(ids)
    remaining = [t for t in _S.session_cron_tasks if t.get("id") not in id_set]
    removed = len(_S.session_cron_tasks) - len(remaining)
    if removed > 0:
        _S.session_cron_tasks = remaining
    return removed


# ===========================================================================
# Trust & persistence
# ===========================================================================


def set_session_trust_accepted(accepted: bool) -> None:
    _S.session_trust_accepted = accepted


def get_session_trust_accepted() -> bool:
    return _S.session_trust_accepted


def set_session_persistence_disabled(disabled: bool) -> None:
    _S.session_persistence_disabled = disabled


def is_session_persistence_disabled() -> bool:
    return _S.session_persistence_disabled


# ===========================================================================
# Plan mode exit tracking
# ===========================================================================


def has_exited_plan_mode_in_session() -> bool:
    return _S.has_exited_plan_mode


def set_has_exited_plan_mode(value: bool) -> None:
    _S.has_exited_plan_mode = value


def needs_plan_mode_exit_attachment() -> bool:
    return _S.needs_plan_mode_exit_attachment


def set_needs_plan_mode_exit_attachment(value: bool) -> None:
    _S.needs_plan_mode_exit_attachment = value


def handle_plan_mode_transition(from_mode: str, to_mode: str) -> None:
    if to_mode == "plan" and from_mode != "plan":
        _S.needs_plan_mode_exit_attachment = False
    if from_mode == "plan" and to_mode != "plan":
        _S.needs_plan_mode_exit_attachment = True


# ===========================================================================
# Auto mode exit tracking
# ===========================================================================


def needs_auto_mode_exit_attachment() -> bool:
    return _S.needs_auto_mode_exit_attachment


def set_needs_auto_mode_exit_attachment(value: bool) -> None:
    _S.needs_auto_mode_exit_attachment = value


def handle_auto_mode_transition(from_mode: str, to_mode: str) -> None:
    if (from_mode == "auto" and to_mode == "plan") or (
        from_mode == "plan" and to_mode == "auto"
    ):
        return
    if to_mode == "auto" and from_mode != "auto":
        _S.needs_auto_mode_exit_attachment = False
    if from_mode == "auto" and to_mode != "auto":
        _S.needs_auto_mode_exit_attachment = True


# ===========================================================================
# LSP recommendation
# ===========================================================================


def has_shown_lsp_recommendation_this_session() -> bool:
    return _S.lsp_recommendation_shown_this_session


def set_lsp_recommendation_shown_this_session(value: bool) -> None:
    _S.lsp_recommendation_shown_this_session = value


# ===========================================================================
# SDK init state
# ===========================================================================


def set_init_json_schema(schema: dict[str, Any]) -> None:
    _S.init_json_schema = schema


def get_init_json_schema() -> Optional[dict[str, Any]]:
    return _S.init_json_schema


# ===========================================================================
# Registered hooks
# ===========================================================================


def register_hook_callbacks(hooks: dict[str, list[Any]]) -> None:
    if _S.registered_hooks is None:
        _S.registered_hooks = {}
    for event, matchers in hooks.items():
        if event not in _S.registered_hooks:
            _S.registered_hooks[event] = []
        _S.registered_hooks[event].extend(matchers)


def get_registered_hooks() -> Optional[dict[str, list[Any]]]:
    return _S.registered_hooks


def clear_registered_hooks() -> None:
    _S.registered_hooks = None


def clear_registered_plugin_hooks() -> None:
    if not _S.registered_hooks:
        return
    filtered: dict[str, list[Any]] = {}
    for event, matchers in _S.registered_hooks.items():
        callback_hooks = [
            m
            for m in matchers
            if not hasattr(m, "pluginRoot")
            and not (isinstance(m, dict) and "pluginRoot" in m)
        ]
        if callback_hooks:
            filtered[event] = callback_hooks
    _S.registered_hooks = filtered if filtered else None


def reset_sdk_init_state() -> None:
    _S.init_json_schema = None
    _S.registered_hooks = None


# ===========================================================================
# Plan slug cache
# ===========================================================================


def get_plan_slug_cache() -> dict[str, str]:
    return _S.plan_slug_cache


# ===========================================================================
# Session created teams
# ===========================================================================


def get_session_created_teams() -> set[str]:
    return _S.session_created_teams


# ===========================================================================
# Teleported session
# ===========================================================================


def set_teleported_session_info(info: dict[str, Any]) -> None:
    _S.teleported_session_info = {
        "isTeleported": True,
        "hasLoggedFirstMessage": False,
        "sessionId": info.get("sessionId"),
    }


def get_teleported_session_info() -> Optional[dict[str, Any]]:
    return _S.teleported_session_info


def mark_first_teleport_message_logged() -> None:
    if _S.teleported_session_info:
        _S.teleported_session_info["hasLoggedFirstMessage"] = True


# ===========================================================================
# Invoked skills
# ===========================================================================


def add_invoked_skill(
    skill_name: str,
    skill_path: str,
    content: str,
    agent_id: Optional[str] = None,
) -> None:
    key = f"{agent_id or ''}:{skill_name}"
    _S.invoked_skills[key] = {
        "skillName": skill_name,
        "skillPath": skill_path,
        "content": content,
        "invokedAt": time.time() * 1000,
        "agentId": agent_id,
    }


def get_invoked_skills() -> dict[str, dict[str, Any]]:
    return _S.invoked_skills


def get_invoked_skills_for_agent(agent_id: Optional[str]) -> dict[str, dict[str, Any]]:
    normalized = agent_id or None
    return {
        k: v for k, v in _S.invoked_skills.items() if v.get("agentId") == normalized
    }


def clear_invoked_skills(preserved_agent_ids: Optional[set[str]] = None) -> None:
    if not preserved_agent_ids:
        _S.invoked_skills.clear()
        return
    to_delete = [
        k
        for k, v in _S.invoked_skills.items()
        if v.get("agentId") is None or v["agentId"] not in preserved_agent_ids
    ]
    for k in to_delete:
        del _S.invoked_skills[k]


def clear_invoked_skills_for_agent(agent_id: str) -> None:
    to_delete = [
        k for k, v in _S.invoked_skills.items() if v.get("agentId") == agent_id
    ]
    for k in to_delete:
        del _S.invoked_skills[k]


# ===========================================================================
# Slow operations tracking (ant-only)
# ===========================================================================

_EMPTY_SLOW_OPERATIONS: list[dict[str, Any]] = []


def add_slow_operation(operation: str, duration_ms: float) -> None:
    if os.environ.get("USER_TYPE") != "ant":
        return
    if "exec" in operation and "claude-prompt-" in operation:
        return
    now = time.time() * 1000
    _S.slow_operations = [
        op for op in _S.slow_operations if now - op["timestamp"] < SLOW_OPERATION_TTL_MS
    ]
    _S.slow_operations.append(
        {"operation": operation, "durationMs": duration_ms, "timestamp": now}
    )
    if len(_S.slow_operations) > MAX_SLOW_OPERATIONS:
        _S.slow_operations = _S.slow_operations[-MAX_SLOW_OPERATIONS:]


def get_slow_operations() -> list[dict[str, Any]]:
    if not _S.slow_operations:
        return _EMPTY_SLOW_OPERATIONS
    now = time.time() * 1000
    if any(now - op["timestamp"] >= SLOW_OPERATION_TTL_MS for op in _S.slow_operations):
        _S.slow_operations = [
            op
            for op in _S.slow_operations
            if now - op["timestamp"] < SLOW_OPERATION_TTL_MS
        ]
        if not _S.slow_operations:
            return _EMPTY_SLOW_OPERATIONS
    return _S.slow_operations


# ===========================================================================
# Main thread agent type
# ===========================================================================


def get_main_thread_agent_type() -> Optional[str]:
    return _S.main_thread_agent_type


def set_main_thread_agent_type(agent_type: Optional[str]) -> None:
    _S.main_thread_agent_type = agent_type


# ===========================================================================
# Remote mode
# ===========================================================================


def get_is_remote_mode() -> bool:
    return _S.is_remote_mode


def set_is_remote_mode(value: bool) -> None:
    _S.is_remote_mode = value


# ===========================================================================
# System prompt section cache
# ===========================================================================


def get_system_prompt_section_cache() -> dict[str, Optional[str]]:
    return _S.system_prompt_section_cache


def set_system_prompt_section_cache_entry(name: str, value: Optional[str]) -> None:
    _S.system_prompt_section_cache[name] = value


def clear_system_prompt_section_state() -> None:
    _S.system_prompt_section_cache.clear()


# ===========================================================================
# Last emitted date
# ===========================================================================


def get_last_emitted_date() -> Optional[str]:
    return _S.last_emitted_date


def set_last_emitted_date(date: Optional[str]) -> None:
    _S.last_emitted_date = date


# ===========================================================================
# Additional directories for CLAUDE.md
# ===========================================================================


def get_additional_directories_for_claude_md() -> list[str]:
    return _S.additional_directories_for_claude_md


def set_additional_directories_for_claude_md(directories: list[str]) -> None:
    _S.additional_directories_for_claude_md = directories


# ===========================================================================
# Channel allowlist
# ===========================================================================


def get_allowed_channels() -> list[dict[str, Any]]:
    return _S.allowed_channels


def set_allowed_channels(entries: list[dict[str, Any]]) -> None:
    _S.allowed_channels = entries


def get_has_dev_channels() -> bool:
    return _S.has_dev_channels


def set_has_dev_channels(value: bool) -> None:
    _S.has_dev_channels = value


# ===========================================================================
# Prompt cache 1h
# ===========================================================================


def get_prompt_cache_1h_allowlist() -> Optional[list[str]]:
    return _S.prompt_cache_1h_allowlist


def set_prompt_cache_1h_allowlist(allowlist: Optional[list[str]]) -> None:
    _S.prompt_cache_1h_allowlist = allowlist


def get_prompt_cache_1h_eligible() -> Optional[bool]:
    return _S.prompt_cache_1h_eligible


def set_prompt_cache_1h_eligible(eligible: Optional[bool]) -> None:
    _S.prompt_cache_1h_eligible = eligible


# ===========================================================================
# Beta header latches
# ===========================================================================


def get_afk_mode_header_latched() -> Optional[bool]:
    return _S.afk_mode_header_latched


def set_afk_mode_header_latched(v: bool) -> None:
    _S.afk_mode_header_latched = v


def get_fast_mode_header_latched() -> Optional[bool]:
    return _S.fast_mode_header_latched


def set_fast_mode_header_latched(v: bool) -> None:
    _S.fast_mode_header_latched = v


def get_cache_editing_header_latched() -> Optional[bool]:
    return _S.cache_editing_header_latched


def set_cache_editing_header_latched(v: bool) -> None:
    _S.cache_editing_header_latched = v


def get_thinking_clear_latched() -> Optional[bool]:
    return _S.thinking_clear_latched


def set_thinking_clear_latched(v: bool) -> None:
    _S.thinking_clear_latched = v


def clear_beta_header_latches() -> None:
    _S.afk_mode_header_latched = None
    _S.fast_mode_header_latched = None
    _S.cache_editing_header_latched = None
    _S.thinking_clear_latched = None


# ===========================================================================
# Prompt ID
# ===========================================================================


def get_prompt_id() -> Optional[str]:
    return _S.prompt_id


def set_prompt_id(id_val: Optional[str]) -> None:
    _S.prompt_id = id_val


# ===========================================================================
# Session metadata setters (for session restore from disk metadata)
# ===========================================================================


def set_agent_name(name: str) -> None:
    _S._agent_name = name


def set_agent_color(color: str) -> None:
    _S._agent_color = color


def set_agent_setting(setting: Any) -> None:
    _S._agent_setting = setting


def set_custom_title(title: str) -> None:
    _S._custom_title = title


def set_tag(tag: str) -> None:
    _S._tag = tag


def set_mode(mode: str) -> None:
    _S._mode = mode


def set_worktree_session(session: Any) -> None:
    _S._worktree_session = session
