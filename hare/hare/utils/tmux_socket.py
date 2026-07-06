"""Isolated tmux socket for Hare (port of tmuxSocket.ts).

WHY THIS EXISTS:
Without isolation, Claude could accidentally affect the user's tmux sessions.
For example, running `tmux kill-session` via the Bash tool would kill the
user's current session if they started Claude from within tmux.

HOW IT WORKS:
1. Claude creates its own tmux socket: `hare-<PID>` (e.g., `hare-12345`)
2. ALL Tmux tool commands use this socket via the `-L` flag
3. ALL Bash tool commands inherit TMUX env var pointing to this socket
   (set in Shell.ts via getClaudeTmuxEnv())

This means ANY tmux command run through Claude - whether via the Tmux tool
directly or via Bash - will operate on Claude's isolated socket, NOT the
user's tmux session.

IMPORTANT: The user's original TMUX env var is NOT used. After socket
initialization, get_hare_tmux_env() returns a value that overrides the
user's TMUX in all child processes.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any

from hare.utils.cleanup_registry import register_cleanup
from hare.utils.debug import log_for_debugging
from hare.utils.exec_file_no_throw import exec_file_no_throw
from hare.utils.log import log_error
from hare.utils.platform import get_platform

TMUX_COMMAND = "tmux"
CLAUDE_SOCKET_PREFIX = "hare"

# ---------------------------------------------------------------------------
# Module-level state — initialized lazily when Tmux tool is first used
# ---------------------------------------------------------------------------
_socket_name: str | None = None
_socket_path: str | None = None
_server_pid: int | None = None
_is_initializing = False
_init_promise: Any = None

# tmux availability — checked once upfront
_tmux_availability_checked = False
_tmux_available = False

# Track whether the Tmux tool has been used at least once.
# Used to defer socket initialization until actually needed.
_tmux_tool_used = False


# ---------------------------------------------------------------------------
# Internal helper — exec tmux, routing through WSL on Windows
# ---------------------------------------------------------------------------

async def _exec_tmux(
    args: list[str],
    *,
    use_cwd: bool = False,
) -> dict[str, Any]:
    """Execute a tmux command, routing through WSL on Windows.

    On Windows, tmux only exists inside WSL — WSL interop lets the tmux
    session launch .exe files as native Win32 processes while stdin/stdout
    flow through the WSL pty.  The ``-e`` flag execs tmux directly without
    the login shell so that ``#`` characters in display-message format
    strings are not eaten by bash as comment markers.
    """
    if get_platform() == "windows":
        result = await exec_file_no_throw(
            "wsl",
            ["-e", TMUX_COMMAND, *args],
            {
                "env": {**os.environ, "WSL_UTF8": "1"},
                "use_cwd": use_cwd,
            },
        )
    else:
        result = await exec_file_no_throw(
            TMUX_COMMAND, args, {"use_cwd": use_cwd}
        )
    return {
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "code": result.get("code") or 0,
    }


# ---------------------------------------------------------------------------
# Public API — socket name / path / info
# ---------------------------------------------------------------------------

def get_hare_socket_name() -> str:
    """Return the unique socket name for this Hare process.

    Format: ``hare-<PID>``
    """
    global _socket_name
    if _socket_name is None:
        _socket_name = f"{CLAUDE_SOCKET_PREFIX}-{os.getpid()}"
    return _socket_name


def get_hare_socket_path() -> str | None:
    """Return the socket path if the socket has been initialized, or None."""
    return _socket_path


def set_hare_socket_info(path: str, pid: int) -> None:
    """Store socket path and server PID after successful initialization."""
    global _socket_path, _server_pid
    _socket_path = path
    _server_pid = pid


def is_socket_initialized() -> bool:
    """Return True when socket path and server PID are both known."""
    return _socket_path is not None and _server_pid is not None


def get_hare_tmux_env() -> str | None:
    """Return the TMUX env-var value that isolates child processes.

    Format: ``<socket_path>,<server_pid>,0``  (matches tmux's TMUX env var).

    Returns None when the socket has not been initialized — callers should
    preserve the user's original TMUX in that case.
    """
    if not _socket_path or _server_pid is None:
        return None
    return f"{_socket_path},{_server_pid},0"


# ---------------------------------------------------------------------------
# tmux availability
# ---------------------------------------------------------------------------

async def check_tmux_available() -> bool:
    """Check whether tmux is installed (cached after first call)."""
    global _tmux_availability_checked, _tmux_available
    if not _tmux_availability_checked:
        if get_platform() == "windows":
            r = await exec_file_no_throw(
                "wsl",
                ["-e", TMUX_COMMAND, "-V"],
                {"env": {**os.environ, "WSL_UTF8": "1"}, "use_cwd": False},
            )
        else:
            r = await exec_file_no_throw("which", [TMUX_COMMAND], {"use_cwd": False})
        _tmux_available = r.get("code") == 0
        if not _tmux_available:
            log_for_debugging(
                "[Socket] tmux is not installed. "
                "The Tmux tool and Teammate tool will not be available."
            )
        _tmux_availability_checked = True
    return _tmux_available


def is_tmux_available() -> bool:
    """Return cached availability.  False if never checked."""
    return _tmux_availability_checked and _tmux_available


# ---------------------------------------------------------------------------
# Tmux-tool usage tracking
# ---------------------------------------------------------------------------

def mark_tmux_tool_used() -> None:
    """Record that the Tmux tool has been used at least once.

    After this call, Shell will initialize the socket for subsequent
    Bash commands so they operate on the isolated socket.
    """
    global _tmux_tool_used
    _tmux_tool_used = True


def has_tmux_tool_been_used() -> bool:
    """Return True if the Tmux tool has been used at least once."""
    return _tmux_tool_used


# ---------------------------------------------------------------------------
# Socket initialization
# ---------------------------------------------------------------------------

async def _do_initialize() -> None:
    """Create the isolated tmux session and populate socket info.

    This is the core of the initialization logic.  It:

    1. Creates a detached ``base`` session on the custom socket.
    2. Falls back to ``has-session`` if ``new-session`` fails (rare edge-case
       where a previous process with the same PID left a socket behind).
    3. Registers a cleanup that kills the tmux server on graceful shutdown.
    4. Sets ``CLAUDE_CODE_SKIP_PROMPT_HISTORY=true`` in the tmux *global*
       environment so child sessions created by TungstenTool inherit it.
    5. On Windows, pins ``WSL_INTEROP`` to the stable symlink so interop
       survives the short-lived ``wsl.exe`` that spawned the server.
    6. Queries ``display-message`` to learn the real socket path and server
       PID, with a fallback that constructs the path from ``$TMPDIR/tmux-$UID``
       if the primary query fails.
    """
    socket = get_hare_socket_name()
    platform = get_platform()

    # 1. Create a detached session -------------------------------------------------
    result = await _exec_tmux([
        "-L", socket,
        "new-session",
        "-d",
        "-s", "base",
        "-e", "CLAUDE_CODE_SKIP_PROMPT_HISTORY=true",
        *(["-e", "WSL_INTEROP=/run/WSL/1_interop"] if platform == "windows" else []),
    ])

    if result["code"] != 0:
        # Session might already exist from a previous run with same PID
        # (unlikely but possible).  Check whether it does.
        check_result = await _exec_tmux([
            "-L", socket,
            "has-session",
            "-t", "base",
        ])
        if check_result["code"] != 0:
            raise RuntimeError(
                f"Failed to create tmux session on socket {socket}: "
                f"{result['stderr']}"
            )

    # 2. Register cleanup on exit --------------------------------------------------
    register_cleanup(_kill_tmux_server_impl)

    # 3. Set global environment variables ------------------------------------------
    # Claude Code instances spawned on this socket inherit these, preventing
    # test/verification sessions from polluting the user's real command history.
    await _exec_tmux([
        "-L", socket,
        "set-environment",
        "-g",
        "CLAUDE_CODE_SKIP_PROMPT_HISTORY",
        "true",
    ])

    # Pin WSL_INTEROP globally too — sessions created by TungstenTool inherit
    # the server's env, which still holds the stale socket from the wsl.exe
    # that spawned the server unless we overwrite it here.
    if platform == "windows":
        await _exec_tmux([
            "-L", socket,
            "set-environment",
            "-g",
            "WSL_INTEROP",
            "/run/WSL/1_interop",
        ])

    # 4. Learn socket path and server PID ------------------------------------------
    info_result = await _exec_tmux([
        "-L", socket,
        "display-message",
        "-p",
        "#{socket_path},#{pid}",
    ])

    if info_result["code"] == 0:
        parts = info_result["stdout"].strip().split(",")
        if len(parts) >= 2 and parts[0] and parts[1]:
            try:
                pid = int(parts[1])
                set_hare_socket_info(parts[0], pid)
                return
            except ValueError:
                pass
        # Parsing failed — log and fall through to fallback
        log_for_debugging(
            f'[Socket] Failed to parse socket info from tmux output: '
            f'"{info_result["stdout"].strip()}". Using fallback path.'
        )
    else:
        log_for_debugging(
            f"[Socket] Failed to get socket info via display-message "
            f'(exit {info_result["code"]}): {info_result["stderr"]}. '
            f"Using fallback path."
        )

    # 5. Fallback: construct socket path from $TMPDIR/tmux-$UID/<socket_name> ------
    # tmux sockets are typically at $TMPDIR/tmux-<UID>/<socket_name> (or
    # /tmp/tmux-<UID>/ if TMPDIR is not set).  On Windows this path is inside
    # WSL, so always use POSIX separators.
    uid = os.getuid() if hasattr(os, "getuid") else 0
    base_tmp_dir = os.environ.get("TMPDIR") or "/tmp"
    fallback_path = posixpath.join(base_tmp_dir, f"tmux-{uid}", socket)

    # Get server PID separately
    pid_result = await _exec_tmux([
        "-L", socket,
        "display-message",
        "-p",
        "#{pid}",
    ])

    if pid_result["code"] == 0:
        try:
            pid = int(pid_result["stdout"].strip())
            log_for_debugging(
                f"[Socket] Using fallback socket path: {fallback_path} "
                f"(server PID: {pid})"
            )
            set_hare_socket_info(fallback_path, pid)
            return
        except ValueError:
            log_for_debugging(
                f'[Socket] Failed to parse server PID from tmux output: '
                f'"{pid_result["stdout"].strip()}"'
            )
    else:
        log_for_debugging(
            f"[Socket] Failed to get server PID "
            f'(exit {pid_result["code"]}): {pid_result["stderr"]}'
        )

    raise RuntimeError(
        f"Failed to get socket info for {socket}: "
        f'primary="{info_result["stderr"]}", '
        f'fallback="{pid_result["stderr"]}"'
    )


async def ensure_socket_initialized() -> None:
    """Ensure the isolated tmux socket exists (idempotent).

    Safe to call multiple times — only initializes once.  Does **not** raise
    on failure; instead it logs the error and degrades gracefully so that
    tmux isolation is simply disabled for the remainder of the session.

    Called by Shell when the Tmux tool has been used or the command includes
    ``tmux``.
    """
    global _is_initializing, _init_promise

    # Already initialized
    if is_socket_initialized():
        return

    # Check if tmux is available before trying to use it
    available = await check_tmux_available()
    if not available:
        return

    # Another call is already initializing — wait for it but swallow errors.
    # The original caller handles the error and sets up graceful degradation.
    if _is_initializing and _init_promise is not None:
        try:
            await _init_promise
        except Exception:
            # Ignore — the original caller logs the error
            pass
        return

    _is_initializing = True
    _init_promise = _do_initialize()

    try:
        await _init_promise
    except Exception as exc:
        # Log error but don't throw — graceful degradation.
        # The tmux isolation feature is disabled; Bash commands run without
        # an overridden TMUX env var.
        log_error(exc if isinstance(exc, Exception) else Exception(str(exc)))
        log_for_debugging(
            f"[Socket] Failed to initialize tmux socket: {exc}. "
            f"Tmux isolation will be disabled."
        )
    finally:
        _is_initializing = False


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def _kill_tmux_server_impl() -> None:
    """Kill the tmux server for Hare's isolated socket (internal)."""
    socket = get_hare_socket_name()
    log_for_debugging(f"[Socket] Killing tmux server for socket: {socket}")

    result = await _exec_tmux(["-L", socket, "kill-server"])

    if result["code"] == 0:
        log_for_debugging("[Socket] Successfully killed tmux server")
    else:
        # Server may already be dead, which is fine
        log_for_debugging(
            f"[Socket] Failed to kill tmux server "
            f'(exit {result["code"]}): {result["stderr"]}'
        )


async def kill_tmux_server() -> None:
    """Kill the tmux server for Hare's isolated socket (public).

    Called during graceful shutdown to clean up resources.
    """
    await _kill_tmux_server_impl()


# ---------------------------------------------------------------------------
# Test / reset support
# ---------------------------------------------------------------------------

def reset_socket_state() -> None:
    """Reset all module-level state to defaults (for testing)."""
    global _socket_name, _socket_path, _server_pid, _is_initializing, _init_promise
    global _tmux_availability_checked, _tmux_available, _tmux_tool_used
    _socket_name = None
    _socket_path = None
    _server_pid = None
    _is_initializing = False
    _init_promise = None
    _tmux_availability_checked = False
    _tmux_available = False
    _tmux_tool_used = False
