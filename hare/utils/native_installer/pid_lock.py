"""
PID-based version locking for the native installer.

Port of: src/utils/nativeInstaller/pidLock.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VersionLockContent:
    pid: int
    version: str
    exec_path: str
    acquired_at: int


def is_pid_based_locking_enabled() -> bool:
    env = os.environ.get("ENABLE_PID_BASED_VERSION_LOCKING")
    if env and env.lower() in ("1", "true", "yes"):
        return True
    if env and env.lower() in ("0", "false", "no"):
        return False
    return False


def read_version_lock(path: Path) -> VersionLockContent | None:
    if not path.is_file():
        return None
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return VersionLockContent(
            pid=int(data["pid"]),
            version=str(data["version"]),
            exec_path=str(data["execPath"]),
            acquired_at=int(data["acquiredAt"]),
        )
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def write_version_lock(path: Path, content: VersionLockContent) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": content.pid,
                "version": content.version,
                "execPath": content.exec_path,
                "acquiredAt": content.acquired_at,
            }
        ),
        encoding="utf-8",
    )


def is_process_alive(pid: int) -> bool:
    """Best-effort POSIX check."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
