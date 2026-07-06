"""
Backend registry – detects and caches the appropriate pane backend.

Port of: src/utils/swarm/backends/registry.ts

The registry manages:
- Detection of the appropriate pane backend (tmux, iTerm2) based on environment
- Registration of backend classes to avoid circular imports
- Caching of backend instances for the lifetime of the process
- TeammateExecutor creation wrapping a detected PaneBackend
- In-process fallback tracking when no pane backend is available
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Callable

from hare.utils.debug import log_for_debugging
from hare.utils.platform import get_platform
from hare.utils.swarm.backends.types import (
    BackendDetectionResult,
    PaneBackend,
    PaneBackendType,
    TeammateExecutor,
)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_cached_backend: PaneBackend | None = None
_cached_detection: BackendDetectionResult | None = None
_cached_in_process_backend: TeammateExecutor | None = None
_cached_pane_backend_executor: TeammateExecutor | None = None
_backends_registered: bool = False
_in_process_fallback: bool = False

# Backend class references – populated by registerTmuxBackend/registerITermBackend
# to avoid circular imports between the registry and backend implementations.
_TmuxBackendClass: Callable[[], PaneBackend] | None = None
_ITermBackendClass: Callable[[], PaneBackend] | None = None


# ---------------------------------------------------------------------------
# Detection helpers (delegate to detection.py equivalents where available)
# ---------------------------------------------------------------------------

def _is_inside_tmux() -> bool:
    """Check if the current process is running inside a tmux session."""
    return bool(os.environ.get("TMUX"))


def _is_inside_tmux_sync() -> bool:
    """Synchronous version of inside-tmux check (used in hot paths)."""
    return bool(os.environ.get("TMUX"))


def _is_in_iterm2() -> bool:
    """Check if the current terminal is iTerm2."""
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def _is_tmux_available() -> bool:
    """Check if tmux is installed and accessible on PATH."""
    return shutil.which("tmux") is not None


def _is_it2_cli_available() -> bool:
    """Check if the it2 CLI tool is installed and accessible on PATH."""
    return shutil.which("it2") is not None


# ---------------------------------------------------------------------------
# Backend class registration (avoids circular imports)
# ---------------------------------------------------------------------------

async def ensure_backends_registered() -> None:
    """
    Lazily imports backend modules so that getBackendByType() can construct
    real backend instances.

    Unlike detectAndGetBackend(), this never spawns subprocesses and never
    throws — it is the lightweight option when you only need class registration
    (e.g., killing a pane by its stored backendType).

    Once called, subsequent calls are no-ops.
    """
    global _backends_registered
    if _backends_registered:
        return
    try:
        import hare.utils.swarm.backends.tmux_backend  # noqa: F401 – triggers registerTmuxBackend
    except ImportError as exc:
        log_for_debugging(f"[registry] tmux_backend import failed: {exc}")
    try:
        import hare.utils.swarm.backends.iterm_backend  # noqa: F401 – triggers registerITermBackend
    except ImportError as exc:
        log_for_debugging(f"[registry] iterm_backend import failed: {exc}")
    _backends_registered = True


def register_tmux_backend(backend_class: Callable[[], PaneBackend]) -> None:
    """
    Registers the TmuxBackend constructor with the registry.
    Called by tmux_backend.py as a module-level side effect to avoid
    circular dependencies between the registry and TmuxBackend.
    """
    global _TmuxBackendClass
    _TmuxBackendClass = backend_class
    log_for_debugging("[registry] TmuxBackend registered")


def register_iterm_backend(backend_class: Callable[[], PaneBackend]) -> None:
    """
    Registers the ITermBackend constructor with the registry.
    Called by iterm_backend.py as a module-level side effect to avoid
    circular dependencies between the registry and ITermBackend.
    """
    global _ITermBackendClass
    _ITermBackendClass = backend_class
    log_for_debugging(
        f"[registry] registerITermBackend called, class={backend_class.__name__ if hasattr(backend_class, '__name__') else 'undefined'}"
    )


def _create_tmux_backend() -> PaneBackend:
    """
    Creates a TmuxBackend instance from the registered class.
    Falls back to a stub if the real backend has not been registered.
    """
    if _TmuxBackendClass is not None:
        return _TmuxBackendClass()
    log_for_debugging("[registry] TmuxBackend not registered, using stub")
    return _TmuxBackendStub()


def _create_iterm_backend() -> PaneBackend:
    """
    Creates an ITermBackend instance from the registered class.
    Falls back to a stub if the real backend has not been registered.
    """
    if _ITermBackendClass is not None:
        return _ITermBackendClass()
    log_for_debugging("[registry] ITermBackend not registered, using stub")
    return _ITermBackendStub()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

async def detect_and_get_backend() -> BackendDetectionResult:
    """
    Detect the appropriate pane backend based on the runtime environment.

    Detection priority flow:
      1. If inside tmux, always use tmux (even in iTerm2)
      2. If in iTerm2 with it2 CLI available, use iTerm2 backend (natively)
      3. If in iTerm2 with user preferring tmux over iTerm2, skip iTerm2 detection
      4. If in iTerm2 without it2 but tmux is available, use tmux as fallback
      5. If in iTerm2 with no it2 and no tmux, error with setup instructions
      6. If tmux is available, use tmux in external-session mode
      7. Otherwise, raise an error with platform-specific install instructions

    The result is cached for the lifetime of the process.
    """
    global _cached_backend, _cached_detection

    # Return cached result if available
    if _cached_detection is not None:
        log_for_debugging(
            f"[BackendRegistry] Using cached backend: {_cached_detection.backend.type}"
        )
        return _cached_detection

    # Ensure backends are registered before detection
    await ensure_backends_registered()

    log_for_debugging("[BackendRegistry] Starting backend detection...")

    inside_tmux = _is_inside_tmux()
    in_iterm2 = _is_in_iterm2()

    log_for_debugging(
        f"[BackendRegistry] Environment: insideTmux={inside_tmux}, inITerm2={in_iterm2}"
    )

    # Priority 1: If inside tmux, always use tmux
    if inside_tmux:
        log_for_debugging(
            "[BackendRegistry] Selected: tmux (running inside tmux session)"
        )
        backend = _create_tmux_backend()
        _cached_backend = backend
        _cached_detection = BackendDetectionResult(
            backend=backend, is_native=True, needs_it2_setup=False
        )
        return _cached_detection

    # Priority 2-5: In iTerm2, try to use native panes
    if in_iterm2:
        # Check if user previously chose to prefer tmux over iTerm2
        prefer_tmux = _get_prefer_tmux_over_iterm2()
        if prefer_tmux:
            log_for_debugging(
                "[BackendRegistry] User prefers tmux over iTerm2, skipping iTerm2 detection"
            )
        else:
            it2_available = _is_it2_cli_available()
            log_for_debugging(
                f"[BackendRegistry] iTerm2 detected, it2 CLI available: {it2_available}"
            )

            if it2_available:
                log_for_debugging(
                    "[BackendRegistry] Selected: iterm2 (native iTerm2 with it2 CLI)"
                )
                backend = _create_iterm_backend()
                _cached_backend = backend
                _cached_detection = BackendDetectionResult(
                    backend=backend, is_native=True, needs_it2_setup=False
                )
                return _cached_detection

        # In iTerm2 but it2 not available - check if tmux can be used as fallback
        tmux_available = _is_tmux_available()
        log_for_debugging(
            f"[BackendRegistry] it2 not available, tmux available: {tmux_available}"
        )

        if tmux_available:
            # Return tmux as fallback. Only signal it2 setup if the user hasn't
            # already chosen to prefer tmux - otherwise they'd be re-prompted.
            log_for_debugging(
                "[BackendRegistry] Selected: tmux (fallback in iTerm2, it2 setup recommended)"
            )
            backend = _create_tmux_backend()
            _cached_backend = backend
            _cached_detection = BackendDetectionResult(
                backend=backend, is_native=False, needs_it2_setup=not prefer_tmux
            )
            return _cached_detection

        # In iTerm2 with no it2 and no tmux - it2 setup is required
        log_for_debugging(
            "[BackendRegistry] ERROR: iTerm2 detected but no it2 CLI and no tmux"
        )
        raise RuntimeError(
            "iTerm2 detected but it2 CLI not installed. "
            "Install it2 with: pip install it2"
        )

    # Priority 6: Fall back to tmux external session
    tmux_available = _is_tmux_available()
    log_for_debugging(
        f"[BackendRegistry] Not in tmux or iTerm2, tmux available: {tmux_available}"
    )

    if tmux_available:
        log_for_debugging("[BackendRegistry] Selected: tmux (external session mode)")
        backend = _create_tmux_backend()
        _cached_backend = backend
        _cached_detection = BackendDetectionResult(
            backend=backend, is_native=False, needs_it2_setup=False
        )
        return _cached_detection

    # Priority 7: No backend available
    log_for_debugging("[BackendRegistry] ERROR: No pane backend available")
    raise RuntimeError(_get_tmux_install_instructions())


def _get_prefer_tmux_over_iterm2() -> bool:
    """
    Checks whether the user has explicitly chosen to prefer tmux over iTerm2
    split panes in their global configuration.

    Returns False (default) if the config cannot be read or the key is not set.
    """
    try:
        from hare.utils.config_full import get_global_config

        config = get_global_config()
        return bool(config.get("preferTmuxOverIterm2", False))
    except Exception as exc:
        log_for_debugging(f"[registry] Failed to read preferTmuxOverIterm2: {exc}")
        return False


def _get_tmux_install_instructions() -> str:
    """Returns platform-specific tmux installation instructions."""
    try:
        platform = get_platform()
    except Exception:
        platform = "unknown"

    if platform == "macos":
        return (
            "To use agent swarms, install tmux:\n"
            "  brew install tmux\n"
            "Then start a tmux session with: tmux new-session -s claude"
        )
    elif platform in ("linux", "wsl"):
        return (
            "To use agent swarms, install tmux:\n"
            "  sudo apt install tmux    # Ubuntu/Debian\n"
            "  sudo dnf install tmux    # Fedora/RHEL\n"
            "Then start a tmux session with: tmux new-session -s claude"
        )
    elif platform == "windows":
        return (
            "To use agent swarms, you need tmux which requires WSL (Windows Subsystem for Linux).\n"
            "Install WSL first, then inside WSL run:\n"
            "  sudo apt install tmux\n"
            "Then start a tmux session with: tmux new-session -s claude"
        )
    else:
        return (
            "To use agent swarms, install tmux using your system's package manager.\n"
            "Then start a tmux session with: tmux new-session -s claude"
        )


# ---------------------------------------------------------------------------
# Cached accessors
# ---------------------------------------------------------------------------

def get_cached_backend() -> PaneBackend | None:
    """Returns the currently cached backend, or None if detection hasn't run yet."""
    return _cached_backend


def get_cached_detection_result() -> BackendDetectionResult | None:
    """
    Returns the cached backend detection result, or None if detection hasn't
    run yet. Use `is_native` to check if teammates are visible in native panes.
    """
    return _cached_detection


# ---------------------------------------------------------------------------
# Backend construction by explicit type
# ---------------------------------------------------------------------------

def get_backend_by_type(backend_type: PaneBackendType) -> PaneBackend:
    """
    Gets a backend instance by explicit type selection.
    Useful for testing or when the user has a preference.

    Args:
        backend_type: The backend type identifier ('tmux' or 'iterm2').

    Returns:
        The requested backend instance.

    Raises:
        ValueError: If the type is unrecognised.
    """
    if backend_type == "tmux":
        return _create_tmux_backend()
    elif backend_type == "iterm2":
        return _create_iterm_backend()
    raise ValueError(
        f"Unknown backend type: {backend_type!r}. Expected 'tmux' or 'iterm2'."
    )


# ---------------------------------------------------------------------------
# Teammate execution mode
# ---------------------------------------------------------------------------

def _get_teammate_mode() -> str:
    """
    Gets the teammate mode for this session.

    Priority:
      1. CLAUDE_TEAMMATE_MODE environment variable (CLI override)
      2. Global config teammateMode setting
      3. Default to 'auto'
    """
    env_mode = os.environ.get("CLAUDE_TEAMMATE_MODE")
    if env_mode in ("auto", "tmux", "in-process"):
        return env_mode
    try:
        from hare.utils.config_full import get_global_config

        config = get_global_config()
        config_mode = config.get("teammateMode")
        if config_mode in ("auto", "tmux", "in-process"):
            return config_mode
    except Exception:
        pass
    return "auto"


def _get_is_non_interactive_session() -> bool:
    """
    Checks if we are running in a non-interactive session (e.g., -p/--print mode).

    Tmux-based teammates don't make sense without a terminal UI, so we force
    in-process in this case.
    """
    try:
        from hare.bootstrap.state import get_is_non_interactive_session

        return get_is_non_interactive_session()
    except ImportError:
        pass
    # Fallback: check common non-interactive signals
    if not sys.stdout.isatty():
        return True
    return os.environ.get("CLAUDE_CODE_NON_INTERACTIVE") == "1"


def is_in_process_enabled() -> bool:
    """
    Checks if in-process teammate execution is enabled.

    Logic:
      - Non-interactive session: always enabled (no terminal UI for panes)
      - If teammate mode is 'in-process': always enabled
      - If teammate mode is 'tmux': always disabled (use pane backend)
      - If teammate mode is 'auto' (default):
        - If a prior spawn fell back to in-process because no pane backend
          was available, stay in-process (so UI reflects reality)
        - If inside tmux or iTerm2, use pane backend (return False)
        - Otherwise, use in-process (return True)
    """
    # Force in-process mode for non-interactive sessions (-p mode)
    if _get_is_non_interactive_session():
        log_for_debugging(
            "[BackendRegistry] isInProcessEnabled: true (non-interactive session)"
        )
        return True

    mode = _get_teammate_mode()

    if mode == "in-process":
        log_for_debugging(
            "[BackendRegistry] isInProcessEnabled: true (mode=in-process)"
        )
        return True

    if mode == "tmux":
        log_for_debugging("[BackendRegistry] isInProcessEnabled: false (mode=tmux)")
        return False

    # 'auto' mode
    # If a prior spawn fell back to in-process, stay in-process
    global _in_process_fallback
    if _in_process_fallback:
        log_for_debugging(
            "[BackendRegistry] isInProcessEnabled: true (fallback after pane backend unavailable)"
        )
        return True

    # Check if a pane backend environment is available
    inside_tmux = _is_inside_tmux_sync()
    in_iterm2 = _is_in_iterm2()
    enabled = not inside_tmux and not in_iterm2

    log_for_debugging(
        f"[BackendRegistry] isInProcessEnabled: {enabled} "
        f"(mode={mode}, insideTmux={inside_tmux}, inITerm2={in_iterm2})"
    )
    return enabled


def get_resolved_teammate_mode() -> str:
    """
    Returns the resolved teammate executor mode for this session.
    Unlike _get_teammate_mode() which may return 'auto', this returns what
    'auto' actually resolves to given the current environment.
    """
    return "in-process" if is_in_process_enabled() else "tmux"


def mark_in_process_fallback() -> None:
    """
    Records that spawn fell back to in-process mode because no pane backend
    was available. After this, isInProcessEnabled() returns True and subsequent
    spawns short-circuit to in-process (the environment won't change mid-session).
    """
    global _in_process_fallback
    log_for_debugging("[BackendRegistry] Marking in-process fallback as active")
    _in_process_fallback = True


# ---------------------------------------------------------------------------
# Executor construction
# ---------------------------------------------------------------------------

def get_in_process_backend() -> TeammateExecutor:
    """
    Gets the InProcessBackend instance.
    Creates and caches the instance on first call.
    If the real InProcessBackend is not importable, returns a stub.
    """
    global _cached_in_process_backend
    if _cached_in_process_backend is not None:
        return _cached_in_process_backend
    try:
        from hare.utils.swarm.backends.in_process_backend import create_in_process_backend

        _cached_in_process_backend = create_in_process_backend()
        log_for_debugging("[BackendRegistry] Created InProcessBackend")
    except ImportError as exc:
        log_for_debugging(f"[BackendRegistry] InProcessBackend not available: {exc}")
        _cached_in_process_backend = _InProcessExecutorStub()
    return _cached_in_process_backend


async def _get_pane_backend_executor() -> TeammateExecutor:
    """
    Gets the PaneBackendExecutor instance wrapping the detected pane backend.
    Creates and caches the instance on first call.
    If the real PaneBackendExecutor is not importable, returns a stub.
    """
    global _cached_pane_backend_executor
    if _cached_pane_backend_executor is not None:
        return _cached_pane_backend_executor
    detection = await detect_and_get_backend()
    try:
        from hare.utils.swarm.backends.pane_backend_executor import (
            create_pane_backend_executor,
        )

        _cached_pane_backend_executor = create_pane_backend_executor(
            detection.backend
        )
        log_for_debugging(
            f"[BackendRegistry] Created PaneBackendExecutor wrapping {detection.backend.type}"
        )
    except ImportError as exc:
        log_for_debugging(f"[BackendRegistry] PaneBackendExecutor not available: {exc}")
        _cached_pane_backend_executor = _PaneExecutorStub()
    return _cached_pane_backend_executor


async def get_teammate_executor(
    prefer_in_process: bool = False,
) -> TeammateExecutor:
    """
    Gets a TeammateExecutor for spawning teammates.

    Returns either:
      - InProcessBackend when prefer_in_process is True and in-process mode is enabled
      - PaneBackendExecutor wrapping the detected pane backend otherwise

    This provides a unified TeammateExecutor interface regardless of execution mode,
    allowing callers to spawn and manage teammates without knowing the backend details.

    Args:
        prefer_in_process: If True and in-process is enabled, returns InProcessBackend.
                           Otherwise returns PaneBackendExecutor.

    Returns:
        A TeammateExecutor instance.
    """
    if prefer_in_process and is_in_process_enabled():
        log_for_debugging("[BackendRegistry] Using in-process executor")
        return get_in_process_backend()

    log_for_debugging("[BackendRegistry] Using pane backend executor")
    return await _get_pane_backend_executor()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def reset_backend_detection() -> None:
    """
    Resets all backend detection caches.
    Used for testing to allow re-detection or to clear stale state.
    """
    global _cached_backend, _cached_detection
    global _cached_in_process_backend, _cached_pane_backend_executor
    global _backends_registered, _in_process_fallback

    _cached_backend = None
    _cached_detection = None
    _cached_in_process_backend = None
    _cached_pane_backend_executor = None
    _backends_registered = False
    _in_process_fallback = False
    log_for_debugging("[BackendRegistry] All caches reset")


# ---------------------------------------------------------------------------
# Fallback stub implementations
#
# These stubs are used when the real backend modules have not been registered
# or are not importable. They satisfy the PaneBackend and TeammateExecutor
# interfaces so the rest of the system can function without panes.
# ---------------------------------------------------------------------------

class _TmuxBackendStub(PaneBackend):
    """Minimal tmux backend stub – satisfies PaneBackend when the real
    TmuxBackend has not been registered."""

    @property
    def type(self) -> PaneBackendType:
        return "tmux"

    @property
    def displayName(self) -> str:
        return "tmux (stub)"

    @property
    def supportsHideShow(self) -> bool:
        return True

    async def is_available(self) -> bool:
        return _is_tmux_available()

    async def is_running_inside(self) -> bool:
        return _is_inside_tmux()

    async def create_pane(self, command: str, cwd: str) -> str:
        log_for_debugging("[TmuxBackendStub] create_pane (no-op)")
        return "tmux-pane-stub"

    async def create_teammate_pane_in_swarm_view(
        self, name: str, color: str
    ) -> dict[str, Any]:
        log_for_debugging(f"[TmuxBackendStub] create_teammate_pane_in_swarm_view name={name}")
        return {"paneId": "tmux-pane-stub", "isFirstTeammate": True}

    async def send_command_to_pane(
        self, pane_id: str, command: str, use_external_session: bool = False
    ) -> None:
        log_for_debugging(f"[TmuxBackendStub] send_command_to_pane {pane_id}")

    async def set_pane_border_color(
        self, pane_id: str, color: str, use_external_session: bool = False
    ) -> None:
        pass

    async def set_pane_title(
        self, pane_id: str, name: str, color: str, use_external_session: bool = False
    ) -> None:
        pass

    async def enable_pane_border_status(
        self, window_target: str | None = None, use_external_session: bool = False
    ) -> None:
        pass

    async def rebalance_panes(self, window_target: str, has_leader: bool) -> None:
        pass

    async def kill_pane(
        self, pane_id: str, use_external_session: bool = False
    ) -> bool:
        log_for_debugging(f"[TmuxBackendStub] kill_pane {pane_id}")
        return True

    async def hide_pane(
        self, pane_id: str, use_external_session: bool = False
    ) -> bool:
        return False

    async def show_pane(
        self, pane_id: str, target_window_or_pane: str, use_external_session: bool = False
    ) -> bool:
        return False

    async def send_keys(self, pane_id: str, keys: str) -> None:
        log_for_debugging(f"[TmuxBackendStub] send_keys {pane_id}: {keys[:30]}...")


class _ITermBackendStub(PaneBackend):
    """Minimal iTerm2 backend stub – satisfies PaneBackend when the real
    ITermBackend has not been registered."""

    @property
    def type(self) -> PaneBackendType:
        return "iterm2"

    @property
    def displayName(self) -> str:
        return "iTerm2 (stub)"

    @property
    def supportsHideShow(self) -> bool:
        return False

    async def is_available(self) -> bool:
        return _is_in_iterm2() and _is_it2_cli_available()

    async def is_running_inside(self) -> bool:
        return _is_in_iterm2()

    async def create_pane(self, command: str, cwd: str) -> str:
        log_for_debugging("[ITermBackendStub] create_pane (no-op)")
        return "iterm2-pane-stub"

    async def create_teammate_pane_in_swarm_view(
        self, name: str, color: str
    ) -> dict[str, Any]:
        log_for_debugging(f"[ITermBackendStub] create_teammate_pane_in_swarm_view name={name}")
        return {"paneId": "iterm2-pane-stub", "isFirstTeammate": True}

    async def send_command_to_pane(
        self, pane_id: str, command: str, use_external_session: bool = False
    ) -> None:
        log_for_debugging(f"[ITermBackendStub] send_command_to_pane {pane_id}")

    async def set_pane_border_color(
        self, pane_id: str, color: str, use_external_session: bool = False
    ) -> None:
        pass

    async def set_pane_title(
        self, pane_id: str, name: str, color: str, use_external_session: bool = False
    ) -> None:
        pass

    async def enable_pane_border_status(
        self, window_target: str | None = None, use_external_session: bool = False
    ) -> None:
        pass

    async def rebalance_panes(self, window_target: str, has_leader: bool) -> None:
        pass

    async def kill_pane(
        self, pane_id: str, use_external_session: bool = False
    ) -> bool:
        log_for_debugging(f"[ITermBackendStub] kill_pane {pane_id}")
        return True

    async def hide_pane(
        self, pane_id: str, use_external_session: bool = False
    ) -> bool:
        return False

    async def show_pane(
        self, pane_id: str, target_window_or_pane: str, use_external_session: bool = False
    ) -> bool:
        return False

    async def send_keys(self, pane_id: str, keys: str) -> None:
        log_for_debugging(f"[ITermBackendStub] send_keys {pane_id}: {keys[:30]}...")


class _InProcessExecutorStub(TeammateExecutor):
    """Minimal in-process executor stub – satisfies TeammateExecutor when the
    real InProcessBackend is not importable."""

    @property
    def type(self) -> str:
        return "in-process"

    async def is_available(self) -> bool:
        return True

    async def spawn(self, config: dict[str, Any]) -> dict[str, Any]:
        log_for_debugging(
            f"[InProcessExecutorStub] spawn name={config.get('name', 'unknown')}"
        )
        return {
            "success": False,
            "agentId": f"{config.get('name', 'unknown')}@{config.get('teamName', '')}",
            "error": "InProcessBackend stub – real implementation not available.",
        }

    async def send_message(self, agent_id: str, message: dict[str, Any]) -> None:
        log_for_debugging(f"[InProcessExecutorStub] send_message to {agent_id}")

    async def terminate(self, agent_id: str, reason: str | None = None) -> bool:
        log_for_debugging(f"[InProcessExecutorStub] terminate {agent_id}")
        return False

    async def kill(self, agent_id: str) -> bool:
        log_for_debugging(f"[InProcessExecutorStub] kill {agent_id}")
        return False

    async def is_active(self, agent_id: str) -> bool:
        return False

    async def stop(self, agent_id: str) -> None:
        log_for_debugging(f"[InProcessExecutorStub] stop {agent_id}")


class _PaneExecutorStub(TeammateExecutor):
    """Minimal pane executor stub – satisfies TeammateExecutor when the
    real PaneBackendExecutor is not importable."""

    @property
    def type(self) -> str:
        return "pane"

    async def is_available(self) -> bool:
        return _is_tmux_available() or (_is_in_iterm2() and _is_it2_cli_available())

    async def spawn(self, config: dict[str, Any]) -> dict[str, Any]:
        log_for_debugging(
            f"[PaneExecutorStub] spawn name={config.get('name', 'unknown')}"
        )
        return {
            "success": False,
            "agentId": f"{config.get('name', 'unknown')}@{config.get('teamName', '')}",
            "paneId": "pane-stub",
            "error": "PaneBackendExecutor stub – real implementation not available.",
        }

    async def send_message(self, agent_id: str, message: dict[str, Any]) -> None:
        log_for_debugging(f"[PaneExecutorStub] send_message to {agent_id}")

    async def terminate(self, agent_id: str, reason: str | None = None) -> bool:
        log_for_debugging(f"[PaneExecutorStub] terminate {agent_id}")
        return False

    async def kill(self, agent_id: str) -> bool:
        log_for_debugging(f"[PaneExecutorStub] kill {agent_id}")
        return False

    async def is_active(self, agent_id: str) -> bool:
        return True

    async def stop(self, agent_id: str) -> None:
        log_for_debugging(f"[PaneExecutorStub] stop {agent_id}")
