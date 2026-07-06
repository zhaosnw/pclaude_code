"""
Cross-platform system directory paths (HOME, Desktop, Documents, Downloads).

Port of: src/utils/systemDirectories.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hare.utils.debug import log_for_debugging
from hare.utils.platform import Platform, get_platform

EnvLike = dict[str, str | None]


@dataclass
class SystemDirectories:
    home: str
    desktop: str
    documents: str
    downloads: str

    def as_dict(self) -> dict[str, str]:
        return {
            "HOME": self.home,
            "DESKTOP": self.desktop,
            "DOCUMENTS": self.documents,
            "DOWNLOADS": self.downloads,
        }


@dataclass
class SystemDirectoriesOptions:
    env: EnvLike | None = None
    homedir: str | None = None
    platform: Platform | None = None


def get_system_directories(
    options: SystemDirectoriesOptions | None = None,
) -> SystemDirectories:
    """Resolve common user directories for Windows, macOS, Linux, and WSL."""
    opts = options or SystemDirectoriesOptions()
    platform = opts.platform if opts.platform is not None else get_platform()
    home_dir = opts.homedir if opts.homedir is not None else str(Path.home())
    env = opts.env if opts.env is not None else dict(os.environ)

    defaults = SystemDirectories(
        home=home_dir,
        desktop=str(Path(home_dir) / "Desktop"),
        documents=str(Path(home_dir) / "Documents"),
        downloads=str(Path(home_dir) / "Downloads"),
    )

    if platform == "windows":
        user_profile = env.get("USERPROFILE") or home_dir
        return SystemDirectories(
            home=home_dir,
            desktop=str(Path(user_profile) / "Desktop"),
            documents=str(Path(user_profile) / "Documents"),
            downloads=str(Path(user_profile) / "Downloads"),
        )

    if platform in ("linux", "wsl"):
        return SystemDirectories(
            home=home_dir,
            desktop=env.get("XDG_DESKTOP_DIR") or defaults.desktop,
            documents=env.get("XDG_DOCUMENTS_DIR") or defaults.documents,
            downloads=env.get("XDG_DOWNLOAD_DIR") or defaults.downloads,
        )

    if platform == "macos":
        return defaults

    log_for_debugging("Unknown platform detected, using default paths")
    return defaults
