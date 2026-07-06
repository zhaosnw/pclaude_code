"""
Bridge types and constants.

Port of: src/bridge/types.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SESSION_TIMEOUT_MS = 24 * 60 * 60 * 1000  # 24 hours

BRIDGE_LOGIN_INSTRUCTION = (
    "Remote Control is only available with claude.ai subscriptions. "
    "Please use `/login` to sign in with your claude.ai account."
)

BRIDGE_LOGIN_ERROR = (
    "Error: You must be logged in to use Remote Control.\n\n" + BRIDGE_LOGIN_INSTRUCTION
)

REMOTE_CONTROL_DISCONNECTED_MSG = "Remote Control disconnected."

# ---------------------------------------------------------------------------
# Work / secret types
# ---------------------------------------------------------------------------


@dataclass
class WorkData:
    type: Literal["session", "healthcheck"] = "session"
    id: str = ""


@dataclass
class WorkResponse:
    id: str = ""
    type: Literal["work"] = "work"
    environment_id: str = ""
    state: str = ""
    data: WorkData = field(default_factory=WorkData)
    secret: str = ""  # base64url-encoded JSON
    created_at: str = ""


@dataclass
class GitInfo:
    type: str = ""
    repo: str = ""
    ref: str = ""
    token: str = ""


@dataclass
class WorkSecretSource:
    type: str = ""
    git_info: Optional[GitInfo] = None


@dataclass
class WorkSecretAuth:
    type: str = ""
    token: str = ""


@dataclass
class WorkSecret:
    version: int = 1
    session_ingress_token: str = ""
    api_base_url: str = ""
    sources: list[WorkSecretSource] = field(default_factory=list)
    auth: list[WorkSecretAuth] = field(default_factory=list)
    claude_code_args: Optional[dict[str, str]] = None
    mcp_config: Optional[Any] = None
    environment_variables: Optional[dict[str, str]] = None
    use_code_sessions: Optional[bool] = None


# ---------------------------------------------------------------------------
# Session types
# ---------------------------------------------------------------------------

SessionDoneStatus = Literal["completed", "failed", "interrupted"]

SessionActivityType = Literal["tool_start", "text", "result", "error"]


@dataclass
class SessionActivity:
    type: SessionActivityType = "text"
    summary: str = ""  # e.g. "Editing src/foo.ts", "Reading package.json"
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Spawn / worker / config types
# ---------------------------------------------------------------------------

SpawnMode = Literal["single-session", "worktree", "same-dir"]

BridgeWorkerType = Literal["claude_code", "claude_code_assistant"]


@dataclass
class BridgeConfig:
    dir: str = ""
    machine_name: str = ""
    branch: str = ""
    git_repo_url: Optional[str] = None
    max_sessions: int = 1
    spawn_mode: SpawnMode = "single-session"
    verbose: bool = False
    sandbox: bool = False
    bridge_id: str = ""  # client-generated UUID
    worker_type: str = "claude_code"  # metadata.worker_type for web filtering
    environment_id: str = ""  # client-generated UUID for idempotent registration
    reuse_environment_id: Optional[str] = None  # backend-issued env id for reconnect
    api_base_url: str = ""
    session_ingress_url: str = ""
    debug_file: Optional[str] = None
    session_timeout_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# API client interface
# ---------------------------------------------------------------------------


@dataclass
class PermissionResponseEvent:
    type: str = "control_response"
    response: dict[str, Any] = field(
        default_factory=lambda: {
            "subtype": "success",
            "request_id": "",
            "response": {},
        }
    )


class BridgeApiClient(Protocol):
    """Mirrors the TS BridgeApiClient interface."""

    async def register_bridge_environment(
        self, config: BridgeConfig
    ) -> dict[str, str]: ...

    async def poll_for_work(
        self,
        environment_id: str,
        environment_secret: str,
        signal: Any = None,
        reclaim_older_than_ms: Optional[int] = None,
    ) -> Optional[WorkResponse]: ...

    async def acknowledge_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> None: ...

    async def stop_work(
        self, environment_id: str, work_id: str, force: bool
    ) -> None: ...

    async def deregister_environment(self, environment_id: str) -> None: ...

    async def send_permission_response_event(
        self, session_id: str, event: PermissionResponseEvent, session_token: str
    ) -> None: ...

    async def archive_session(self, session_id: str) -> None: ...

    async def reconnect_session(self, environment_id: str, session_id: str) -> None: ...

    async def heartbeat_work(
        self, environment_id: str, work_id: str, session_token: str
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Session handle
# ---------------------------------------------------------------------------


class SessionHandle(Protocol):
    session_id: str
    done: Any  # asyncio.Task / Future that resolves to SessionDoneStatus
    activities: list[SessionActivity]
    current_activity: Optional[SessionActivity]
    access_token: str
    last_stderr: list[str]

    def kill(self) -> None: ...
    def force_kill(self) -> None: ...
    def write_stdin(self, data: str) -> None: ...
    def update_access_token(self, token: str) -> None: ...


# ---------------------------------------------------------------------------
# Session spawner
# ---------------------------------------------------------------------------


@dataclass
class SessionSpawnOpts:
    session_id: str = ""
    sdk_url: str = ""
    access_token: str = ""
    use_ccr_v2: bool = False
    worker_epoch: Optional[int] = None
    on_first_user_message: Any = None  # callable(str) or None


class SessionSpawner(Protocol):
    def spawn(self, opts: SessionSpawnOpts, directory: str) -> SessionHandle: ...


# ---------------------------------------------------------------------------
# Bridge logger interface
# ---------------------------------------------------------------------------


class BridgeLogger(Protocol):
    """Mirrors the TS BridgeLogger interface."""

    def print_banner(self, config: BridgeConfig, environment_id: str) -> None: ...
    def log_session_start(self, session_id: str, prompt: str) -> None: ...
    def log_session_complete(self, session_id: str, duration_ms: int) -> None: ...
    def log_session_failed(self, session_id: str, error: str) -> None: ...
    def log_status(self, message: str) -> None: ...
    def log_verbose(self, message: str) -> None: ...
    def log_error(self, message: str) -> None: ...
    def log_reconnected(self, disconnected_ms: int) -> None: ...
    def update_idle_status(self) -> None: ...
    def update_reconnecting_status(self, delay_str: str, elapsed_str: str) -> None: ...
    def update_session_status(
        self,
        session_id: str,
        elapsed: str,
        activity: SessionActivity,
        trail: list[str],
    ) -> None: ...
    def clear_status(self) -> None: ...
    def set_repo_info(self, repo_name: str, branch: str) -> None: ...
    def set_debug_log_path(self, path: str) -> None: ...
    def set_attached(self, session_id: str) -> None: ...
    def update_failed_status(self, error: str) -> None: ...
    def toggle_qr(self) -> None: ...
    def update_session_count(
        self, active: int, max_sessions: int, mode: SpawnMode
    ) -> None: ...
    def set_spawn_mode_display(
        self, mode: Optional[Literal["same-dir", "worktree"]]
    ) -> None: ...
    def add_session(self, session_id: str, url: str) -> None: ...
    def update_session_activity(
        self, session_id: str, activity: SessionActivity
    ) -> None: ...
    def set_session_title(self, session_id: str, title: str) -> None: ...
    def remove_session(self, session_id: str) -> None: ...
    def refresh_display(self) -> None: ...


# ---------------------------------------------------------------------------
# Backoff config
# ---------------------------------------------------------------------------


@dataclass
class BackoffConfig:
    conn_initial_ms: int = 1000
    conn_cap_ms: int = 30000
    conn_give_up_ms: int = 300000
    general_initial_ms: int = 1000
    general_cap_ms: int = 30000
    general_give_up_ms: int = 60000
    shutdown_grace_ms: int = 5000
    stop_work_base_delay_ms: int = 2000
