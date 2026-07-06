"""Detect system package manager for Hare CLI installs.

Port of: src/utils/nativeInstaller/packageManagers.ts
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

PackageManager = Literal[
    "homebrew",
    "winget",
    "pacman",
    "deb",
    "rpm",
    "apk",
    "mise",
    "asdf",
    "unknown",
]


@lru_cache(maxsize=1)
def get_os_release() -> dict[str, str | list[str]] | None:
    p = Path("/etc/os-release")
    if not p.is_file():
        return None
    try:
        content = p.read_text(encoding="utf-8")
    except OSError:
        return None
    id_m = re.search(r'^ID=["\']?(\S+?)["\']?\s*$', content, re.MULTILINE)
    id_like_m = re.search(r'^ID_LIKE=["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    id_like = id_like_m.group(1).split() if id_like_m else []
    return {"id": id_m.group(1) if id_m else "", "idLike": id_like}


def detect_mise() -> bool:
    exec_path = sys.executable or sys.argv[0]
    return bool(re.search(r"[/\\]mise[/\\]installs[/\\]", exec_path, re.I))


def detect_asdf() -> bool:
    exec_path = sys.executable or sys.argv[0]
    return bool(re.search(r"[/\\]\.asdf[/\\]installs[/\\]", exec_path, re.I))


def detect_package_manager() -> PackageManager:
    if sys.platform == "win32":
        return "winget"
    if detect_mise():
        return "mise"
    if detect_asdf():
        return "asdf"
    return "unknown"
