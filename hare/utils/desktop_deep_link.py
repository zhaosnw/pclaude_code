"""Open Hare Desktop via `hare://` deep link (`desktopDeepLink.ts`)."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlunparse

from hare.utils.cwd import get_cwd
from hare.utils.debug import log_for_debugging

MIN_DESKTOP_VERSION = "1.1.2396"

try:
    from packaging.version import Version
except ImportError:
    Version = None  # type: ignore[misc, assignment]


def _is_dev_mode() -> bool:
    if os.environ.get("NODE_ENV") == "development":
        return True
    paths = [sys.argv[0] or "", sys.executable or ""]
    build_dirs = (
        "/build-ant/",
        "/build-ant-native/",
        "/build-external/",
        "/build-external-native/",
    )
    return any(any(d in p for d in build_dirs) for p in paths if p)


def _build_desktop_deep_link(session_id: str) -> str:
    scheme = "hare-dev" if _is_dev_mode() else "hare"
    query = urlencode({"session": session_id, "cwd": get_cwd()})
    return urlunparse((scheme, "resume", "", "", query, ""))


def _run(cmd: list[str]) -> int:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode
    except OSError:
        return 1


async def _is_desktop_installed() -> bool:
    if _is_dev_mode():
        return True
    if sys.platform == "darwin":
        return Path("/Applications/Hare.app").exists()
    if sys.platform.startswith("linux"):
        r = _run(["xdg-mime", "query", "default", "x-scheme-handler/hare"])
        return r == 0
    if sys.platform == "win32":
        r = _run(["reg", "query", "HKEY_CLASSES_ROOT\\hare", "/ve"])
        return r == 0
    return False


async def _get_desktop_version() -> str | None:
    if sys.platform == "darwin":
        r = subprocess.run(
            [
                "defaults",
                "read",
                "/Applications/Hare.app/Contents/Info.plist",
                "CFBundleShortVersionString",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return None
        v = (r.stdout or "").strip()
        return v or None
    if sys.platform == "win32":
        lad = os.environ.get("LOCALAPPDATA")
        if not lad:
            return None
        install = Path(lad) / "AnthropicClaude"
        try:
            names = [
                e.name[4:]
                for e in install.iterdir()
                if e.is_dir() and e.name.startswith("app-")
            ]
        except OSError:
            return None
        if not names:
            return None
        if Version:

            def key(x: str) -> Version:
                try:
                    return Version(x)
                except Exception:  # noqa: BLE001
                    return Version("0")

            names.sort(key=key)
        else:
            names.sort()
        return names[-1]
    return None


@dataclass
class DesktopNotInstalled:
    status: Literal["not-installed"]


@dataclass
class DesktopVersionOld:
    status: Literal["version-too-old"]
    version: str


@dataclass
class DesktopReady:
    status: Literal["ready"]
    version: str


DesktopInstallStatus = DesktopNotInstalled | DesktopVersionOld | DesktopReady


def _semver_gte(a: str, b: str) -> bool:
    if Version:
        try:
            return Version(a) >= Version(b)
        except Exception:  # noqa: BLE001
            return True
    return a >= b


async def get_desktop_install_status() -> DesktopInstallStatus:
    if not await _is_desktop_installed():
        return DesktopNotInstalled("not-installed")
    try:
        version = await _get_desktop_version()
    except Exception:  # noqa: BLE001
        return DesktopReady("ready", "unknown")
    if not version:
        return DesktopReady("ready", "unknown")
    if not _semver_gte(version, MIN_DESKTOP_VERSION):
        return DesktopVersionOld("version-too-old", version)
    return DesktopReady("ready", version)


async def _open_deep_link(deep_link_url: str) -> bool:
    log_for_debugging(f"Opening deep link: {deep_link_url}")
    if sys.platform == "darwin":
        if _is_dev_mode():
            r = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "Electron" to open location "{deep_link_url}"',
                ],
                capture_output=True,
                timeout=30,
            )
            return r.returncode == 0
        r = subprocess.run(["open", deep_link_url], capture_output=True, timeout=30)
        return r.returncode == 0
    if sys.platform.startswith("linux"):
        r = subprocess.run(["xdg-open", deep_link_url], capture_output=True, timeout=30)
        return r.returncode == 0
    if sys.platform == "win32":
        r = subprocess.run(
            ["cmd", "/c", "start", "", deep_link_url], capture_output=True, timeout=30
        )
        return r.returncode == 0
    return False


async def open_current_session_in_desktop(session_id: str) -> dict[str, Any]:
    if not await _is_desktop_installed():
        return {
            "success": False,
            "error": "Hare Desktop is not installed. Install it from https://hare.ai/download",
        }
    url = _build_desktop_deep_link(session_id)
    opened = await _open_deep_link(url)
    if not opened:
        return {
            "success": False,
            "error": "Failed to open Hare Desktop. Please try opening it manually.",
            "deep_link_url": url,
        }
    return {"success": True, "deep_link_url": url}
