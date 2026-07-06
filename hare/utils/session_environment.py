"""Session hook environment scripts on disk (port of sessionEnvironment.ts)."""

from __future__ import annotations

import errno
import os
import re
from pathlib import Path

from hare.bootstrap import state as bootstrap_state
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.errors import error_message
from hare.utils.platform import get_platform

_UNSET = object()
_session_env_script: str | None | object = _UNSET

HOOK_ENV_PRIORITY: dict[str, int] = {
    "setup": 0,
    "sessionstart": 1,
    "cwdchanged": 2,
    "filechanged": 3,
}
HOOK_ENV_REGEX = re.compile(
    r"^(setup|sessionstart|cwdchanged|filechanged)-hook-(\d+)\.sh$"
)


def _hook_env_sort_key(name: str) -> tuple[int, int]:
    m = HOOK_ENV_REGEX.match(name)
    if not m:
        return (99, 0)
    t, idx = m.group(1), m.group(2)
    return (HOOK_ENV_PRIORITY.get(t, 99), int(idx))


async def get_session_env_dir_path() -> str:
    session_env_dir = (
        Path(get_hare_config_home_dir())
        / "session-env"
        / bootstrap_state.get_session_id()
    )
    session_env_dir.mkdir(parents=True, exist_ok=True)
    return str(session_env_dir)


async def get_hook_env_file_path(
    hook_event: str,
    hook_index: int,
) -> str:
    prefix = hook_event.lower()
    base = await get_session_env_dir_path()
    return str(Path(base) / f"{prefix}-hook-{hook_index}.sh")


async def clear_cwd_env_files() -> None:
    try:
        dir_path = await get_session_env_dir_path()
        p = Path(dir_path)
        if not p.is_dir():
            return
        for f in p.iterdir():
            if not f.is_file():
                continue
            n = f.name
            if (
                n.startswith("filechanged-hook-") or n.startswith("cwdchanged-hook-")
            ) and HOOK_ENV_REGEX.match(n):
                f.write_text("", encoding="utf-8")
    except OSError as e:
        if e.errno != errno.ENOENT:
            log_for_debugging(f"Failed to clear cwd env files: {error_message(e)}")


def invalidate_session_env_cache() -> None:
    global _session_env_script
    log_for_debugging("Invalidating session environment cache")
    _session_env_script = _UNSET  # type: ignore[assignment]


async def get_session_environment_script() -> str | None:
    global _session_env_script
    if get_platform() == "windows":
        log_for_debugging("Session environment not yet supported on Windows")
        return None

    if _session_env_script is not _UNSET:
        return _session_env_script  # type: ignore[return-value]

    scripts: list[str] = []

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            env_script = Path(env_file).read_text(encoding="utf-8").strip()
            if env_script:
                scripts.append(env_script)
                log_for_debugging(
                    f"Session environment loaded from CLAUDE_ENV_FILE: {env_file} ({len(env_script)} chars)"
                )
        except OSError as e:
            if e.errno != errno.ENOENT:
                log_for_debugging(f"Failed to read CLAUDE_ENV_FILE: {error_message(e)}")

    session_env_dir = await get_session_env_dir_path()
    try:
        files = sorted(
            (f for f in os.listdir(session_env_dir) if HOOK_ENV_REGEX.match(f)),
            key=_hook_env_sort_key,
        )
        for fname in files:
            fp = Path(session_env_dir) / fname
            try:
                content = fp.read_text(encoding="utf-8").strip()
                if content:
                    scripts.append(content)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    log_for_debugging(
                        f"Failed to read hook file {fp}: {error_message(e)}"
                    )
        if files:
            log_for_debugging(
                f"Session environment loaded from {len(files)} hook file(s)"
            )
    except OSError as e:
        if e.errno != errno.ENOENT:
            log_for_debugging(
                f"Failed to load session environment from hooks: {error_message(e)}"
            )

    if not scripts:
        log_for_debugging("No session environment scripts found")
        _session_env_script = None
        return None

    joined = "\n".join(scripts)
    _session_env_script = joined
    log_for_debugging(f"Session environment script ready ({len(joined)} chars total)")
    return joined
