"""WSL / Windows IDE path conversion — port of `idePathConversion.ts`."""

from __future__ import annotations

import re
import subprocess
from typing import Protocol


class IDEPathConverter(Protocol):
    def to_local_path(self, ide_path: str) -> str: ...
    def to_ide_path(self, local_path: str) -> str: ...


_WSL_UNC = re.compile(r"^\\\\wsl(?:\.localhost|\$)\\([^\\]+)(.*)$")


class WindowsToWSLConverter:
    def __init__(self, wsl_distro_name: str | None) -> None:
        self._distro = wsl_distro_name

    def to_local_path(self, windows_path: str) -> str:
        if not windows_path:
            return windows_path
        if self._distro:
            m = _WSL_UNC.match(windows_path)
            if m and m.group(1) != self._distro:
                return windows_path
        try:
            return subprocess.run(
                ["wslpath", "-u", windows_path],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            s = windows_path.replace("\\", "/")
            m2 = re.match(r"^([A-Za-z]):", s)
            if m2:
                letter = m2.group(1).lower()
                return "/mnt/" + letter + s[2:]
            return windows_path

    def to_ide_path(self, wsl_path: str) -> str:
        if not wsl_path:
            return wsl_path
        try:
            return subprocess.run(
                ["wslpath", "-w", wsl_path],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return wsl_path


def check_wsl_distro_match(windows_path: str, wsl_distro_name: str) -> bool:
    m = _WSL_UNC.match(windows_path)
    if m:
        return m.group(1) == wsl_distro_name
    return True
