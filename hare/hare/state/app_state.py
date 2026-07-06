"""
Application state — full state management with subscriptions.

Port of: src/state/AppState.tsx + AppStateStore.ts (44KB combined)

Single source of truth for all session-scoped UI + session state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Core AppState — matching TS AppState type
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    # Settings & model
    settings: dict[str, Any] = field(default_factory=dict)
    verbose: bool = False
    main_loop_model: str = ""
    main_loop_model_for_session: str = ""
    main_loop_model_override: str = ""

    # UI state
    status_line_text: str | None = None
    expanded_view: str = "none"  # 'none' | 'tasks' | 'teammates'
    is_brief_only: bool = False
    show_teammate_message_preview: bool = False
    selected_ip_agent_index: int = 0
    coordinator_task_index: int = -1
    view_selection_mode: str = "none"
    footer_selection: str | None = None
    spinner_tip: str | None = None
    agent: str | None = None

    # Permissions
    tool_permission_context: dict[str, Any] = field(
        default_factory=lambda: {
            "cwd": "",
            "additionalWorkingDirectories": {},
            "alwaysAllowRules": {},
            "denyRules": [],
            "permissionMode": "default",
        }
    )
    permission_mode: str = "default"
    session_bypass_permissions_mode: bool = False

    # Feature modes
    kairos_enabled: bool = False
    assistant_enabled: bool = False
    sandbox_enabled: bool = False
    vim_mode: bool = False
    fast_mode: bool = False
    plan_mode: bool = False
    auto_mode: bool = False
    output_style: str = "default"

    # Remote & bridge
    remote_session_url: str | None = None
    remote_connection_status: str = "disconnected"
    remote_background_task_count: int = 0
    repl_bridge_enabled: bool = False
    repl_bridge_explicit: bool = False
    repl_bridge_outbound_only: bool = False
    repl_bridge_connected: bool = False
    repl_bridge_session_active: bool = False
    repl_bridge_reconnecting: bool = False
    repl_bridge_connect_url: str | None = None
    repl_bridge_session_url: str | None = None
    repl_bridge_environment_id: str | None = None

    # Session metadata
    session_id: str = ""
    project_dir: str = ""
    is_processing: bool = False
    agent_id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)

    # Tools & MCP
    tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_clients: list[dict[str, Any]] = field(default_factory=list)
    mcp_tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_commands: list[dict[str, Any]] = field(default_factory=list)
    mcp_resources: dict[str, Any] = field(default_factory=dict)
    mcp_plugin_reconnect_key: int = 0

    # Tasks
    tasks: dict[str, Any] = field(default_factory=dict)
    running_tasks_count: int = 0

    # Cost & usage
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)

    # Attribution
    attribution: dict[str, Any] = field(default_factory=dict)

    # Standalone agent context
    standalone_agent_context: dict[str, Any] | None = None

    # File history
    file_history: dict[str, Any] = field(
        default_factory=lambda: {
            "snapshots": [],
            "trackedFiles": [],
            "snapshotSequence": 0,
        }
    )

    # Plan/auto mode exit tracking
    has_exited_plan_mode: bool = False
    needs_plan_mode_exit_attachment: bool = False
    needs_auto_mode_exit_attachment: bool = False

    # LSP recommendation
    lsp_recommendation_shown_this_session: bool = False

    # Plugin state
    loaded_plugins: list[dict[str, Any]] = field(default_factory=list)
    plugin_errors: list[dict[str, Any]] = field(default_factory=list)

    # Agent definitions
    agent_definitions: list[dict[str, Any]] = field(default_factory=list)

    # Notifications
    notifications: list[dict[str, Any]] = field(default_factory=list)

    # Prompt suggestion
    prompt_suggestion: str | None = None

    # Effort level
    effort: str = "medium"

    # Thinking
    thinking_enabled: bool = True
    max_thinking_tokens: int | None = None


# ---------------------------------------------------------------------------
# AppStateStore — global store with subscriptions
# ---------------------------------------------------------------------------

_global_state: AppState | None = None
_subscribers: list[Callable[[AppState], None]] = []


def get_app_state() -> AppState:
    """Get the global app state. Lazy-init on first access."""
    global _global_state
    if _global_state is None:
        _global_state = AppState()
    return _global_state


def set_app_state(state_or_updater: AppState | Callable[[AppState], AppState]) -> None:
    """Set or update the global app state. Accepts a new state or an updater function."""
    global _global_state
    if callable(state_or_updater):
        if _global_state is None:
            _global_state = AppState()
        new_state = state_or_updater(_global_state)
    else:
        new_state = state_or_updater
    _global_state = new_state
    _notify_subscribers(new_state)


def subscribe(handler: Callable[[AppState], None]) -> Callable[[], None]:
    """Subscribe to state changes. Returns unsubscribe function."""
    _subscribers.append(handler)

    def unsubscribe() -> None:
        if handler in _subscribers:
            _subscribers.remove(handler)

    return unsubscribe


def _notify_subscribers(state: AppState) -> None:
    for handler in _subscribers:
        try:
            handler(state)
        except Exception:
            pass


def reset_app_state() -> None:
    """Reset to fresh initial state (for testing)."""
    global _global_state
    _global_state = AppState()
    _subscribers.clear()
