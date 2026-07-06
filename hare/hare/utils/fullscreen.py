"""
Fullscreen / flicker-free terminal mode flags (no Ink in Python port).

Port of: src/utils/fullscreen.ts
"""

from __future__ import annotations

import os
import subprocess

from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import is_env_defined_falsy, is_env_truthy
from hare.utils.exec_file_no_throw import exec_file_no_throw

_tmux_control_mode_probed: bool | None = None
_logged_tmux_cc_disable = False
_checked_tmux_mouse_hint = False


def _reset_tmux_control_mode_probe_for_testing() -> None:
    global _tmux_control_mode_probed, _logged_tmux_cc_disable
    _tmux_control_mode_probed = None
    _logged_tmux_cc_disable = False


def _is_tmux_control_mode_env_heuristic() -> bool:
    if not os.environ.get("TMUX"):
        return False
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return False
    term = os.environ.get("TERM") or ""
    return not term.startswith("screen") and not term.startswith("tmux")


def _probe_tmux_control_mode_sync() -> None:
    global _tmux_control_mode_probed
    _tmux_control_mode_probed = _is_tmux_control_mode_env_heuristic()
    if _tmux_control_mode_probed:
        return
    if not os.environ.get("TMUX"):
        return
    if os.environ.get("TERM_PROGRAM"):
        return
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#{client_control_mode}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if r.returncode != 0:
        return
    _tmux_control_mode_probed = r.stdout.strip() == "1"


def is_tmux_control_mode() -> bool:
    global _tmux_control_mode_probed
    if _tmux_control_mode_probed is None:
        _probe_tmux_control_mode_sync()
    return bool(_tmux_control_mode_probed)


def is_fullscreen_env_enabled() -> bool:
    global _logged_tmux_cc_disable
    if is_env_defined_falsy(os.environ.get("CLAUDE_CODE_NO_FLICKER")):
        return False
    if is_env_truthy(os.environ.get("CLAUDE_CODE_NO_FLICKER")):
        return True
    if is_tmux_control_mode():
        if not _logged_tmux_cc_disable:
            _logged_tmux_cc_disable = True
            log_for_debugging(
                "fullscreen disabled: tmux -CC (iTerm2 integration mode) detected · "
                "set CLAUDE_CODE_NO_FLICKER=1 to override"
            )
        return False
    return os.environ.get("USER_TYPE") == "ant"


def is_mouse_tracking_enabled() -> bool:
    return not is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_MOUSE"))


def is_mouse_clicks_disabled() -> bool:
    return is_env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_MOUSE_CLICKS"))


def get_is_interactive() -> bool:
    from hare.bootstrap.state import get_is_non_interactive_session

    return not get_is_non_interactive_session()


def is_fullscreen_active() -> bool:
    return get_is_interactive() and is_fullscreen_env_enabled()


async def maybe_get_tmux_mouse_hint() -> str | None:
    global _checked_tmux_mouse_hint
    if not os.environ.get("TMUX"):
        return None
    if not is_fullscreen_active() or is_tmux_control_mode():
        return None
    if _checked_tmux_mouse_hint:
        return None
    _checked_tmux_mouse_hint = True
    r = await exec_file_no_throw(
        "tmux", ["show", "-Av", "mouse"], {"useCwd": False, "timeout": 2000}
    )
    if r["code"] != 0 or (r["stdout"] or "").strip() == "on":
        return None
    return "tmux detected · scroll with PgUp/PgDn · or add 'set -g mouse on' to ~/.tmux.conf for wheel scroll"


def _reset_for_testing() -> None:
    global _logged_tmux_cc_disable, _checked_tmux_mouse_hint
    _logged_tmux_cc_disable = False
    _checked_tmux_mouse_hint = False
