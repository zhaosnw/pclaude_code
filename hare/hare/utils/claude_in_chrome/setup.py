"""Port of: src/utils/claudeInChrome/setup.ts + setupPortable.ts

Chrome native-messaging host setup, teardown, detection, and validation.
Supports macOS, Linux, Windows, and WSL.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hare.utils.claude_in_chrome.common import (
    BROWSER_DETECTION_ORDER,
    CHROMIUM_BROWSERS,
    CHROME_EXTENSION_RECONNECT_URL,
    NATIVE_HOST_IDENTIFIER,
    NATIVE_HOST_MANIFEST_NAME,
    ChromiumBrowser,
    get_all_browser_data_paths,
    get_allowed_origins,
)
from hare.utils.claude_in_chrome.setup_portable import is_chrome_extension_installed_portable
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.log import log_error_msg, log_warning


def _platform() -> str:
    """Normalize sys.platform -> macos | linux | windows | wsl."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "linux":
        try:
            if "microsoft" in os.uname().release.lower():
                return "wsl"
        except Exception:
            pass
        return "linux"
    return "linux"


def get_native_host_manifest_path() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Google/Chrome/NativeMessagingHosts"
        )
    if sys.platform == "win32":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google", "Chrome", "User Data", "NativeMessagingHosts",
        )
    return os.path.expanduser("~/.config/google-chrome/NativeMessagingHosts")


def get_native_messaging_hosts_dirs() -> list[str]:
    """All browser NativeMessagingHosts directories for the current platform."""
    p = _platform()
    home = Path.home()
    dirs: list[str] = []
    if p in ("windows", "wsl"):
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Local"))
        dirs.append(str(Path(appdata) / "Claude Code" / "ChromeNativeHost"))
        return dirs
    for bid in BROWSER_DETECTION_ORDER:
        cfg = CHROMIUM_BROWSERS[bid]
        key = "macos" if p == "macos" else "linux"
        segs = cfg.get(key, {}).get("native_messaging_path", [])
        if segs:
            dirs.append(str(home.joinpath(*segs)))
    return dirs


# ---------------------------------------------------------------------------
# Browser detection
# ---------------------------------------------------------------------------

def _find_browser_executable(browser_id: ChromiumBrowser) -> str | None:
    """Locate a browser binary on the current platform, or None if not installed."""
    p = _platform()
    if p == "macos":
        app_name: str = CHROMIUM_BROWSERS[browser_id].get("macos", {}).get("app_name", "")
        if not app_name:
            return None
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidate = root / f"{app_name}.app"
            if candidate.exists():
                return str(candidate)
        return None

    if p in ("linux", "wsl"):
        binaries: list[str] = CHROMIUM_BROWSERS[browser_id].get("linux", {}).get("binaries", [])
        for binary in binaries:
            found = shutil.which(binary)
            if found:
                return found
        return None

    # Windows
    cfg = CHROMIUM_BROWSERS[browser_id].get("windows", {})
    if not cfg:
        return None
    name = CHROMIUM_BROWSERS[browser_id].get("name", "")
    for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if not root:
            continue
        candidate = Path(root) / name / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return None


def get_installed_browsers() -> list[ChromiumBrowser]:
    """Return supported Chromium browsers actually installed on this machine."""
    installed: list[ChromiumBrowser] = []
    p = _platform()
    for bid in BROWSER_DETECTION_ORDER:
        cfg = CHROMIUM_BROWSERS[bid]
        key = "macos" if p == "macos" else ("linux" if p in ("linux", "wsl") else "windows")
        if cfg.get(key) and _find_browser_executable(bid) is not None:
            installed.append(bid)
    return installed


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

def should_enable_claude_in_chrome(chrome_flag: bool | None = None) -> bool:
    """Priority: CLI flag > CLAUDE_CODE_ENABLE_CFC env var > config file > default False."""
    if chrome_flag is not None:
        return chrome_flag
    env = os.environ.get("CLAUDE_CODE_ENABLE_CFC")
    if is_env_truthy(env):
        return True
    if env is not None and env.lower() in ("0", "false", "no"):
        return False
    config_file = Path(get_hare_config_home_dir()) / "chrome" / "enabled"
    try:
        if config_file.exists():
            return config_file.read_text("utf-8").strip().lower() in ("1", "true", "yes")
    except (OSError, UnicodeDecodeError):
        pass
    return False


# ---------------------------------------------------------------------------
# Wrapper script
# ---------------------------------------------------------------------------

async def create_wrapper_script(command: str) -> str:
    """Write a sh/bat trampoline script in ~/.hare/chrome/. Chrome's native
    host manifest 'path' cannot contain arguments, so the wrapper runs the
    real command. Returns the absolute path to the wrapper."""
    chrome_dir = Path(get_hare_config_home_dir()) / "chrome"
    is_win = _platform() in ("windows",)
    if is_win:
        wrapper = chrome_dir / "chrome-native-host.bat"
        content = (
            "@echo off\r\n"
            "REM Chrome native host wrapper\r\n"
            "REM Generated by Claude Code - do not edit\r\n"
            f"{command}\r\n"
        )
    else:
        wrapper = chrome_dir / "chrome-native-host"
        content = (
            "#!/bin/sh\n"
            "# Chrome native host wrapper\n"
            "# Generated by Claude Code - do not edit\n"
            f"exec {command}\n"
        )
    try:
        if wrapper.read_text("utf-8") == content:
            return str(wrapper)
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    chrome_dir.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(content, "utf-8")
    if not is_win:
        os.chmod(wrapper, 0o755)
    return str(wrapper)


# ---------------------------------------------------------------------------
# Manifest validation, install, uninstall
# ---------------------------------------------------------------------------

def _validate_manifest_structure(data: dict) -> bool:
    """Check that a manifest dict has the required Chrome native-host keys."""
    required = {"name", "description", "path", "type", "allowed_origins"}
    return (required.issubset(data.keys())
            and data.get("type") == "stdio"
            and isinstance(data.get("allowed_origins"), list)
            and bool(data.get("path"))
            and bool(data.get("name")))


async def install_chrome_native_host_manifest(manifest_binary_path: str) -> bool:
    """Install manifest JSON to each browser's NativeMessagingHosts directory.
    Returns True if at least one manifest was written or updated."""
    dirs = get_native_messaging_hosts_dirs()
    if not dirs:
        log_warning("[Claude in Chrome] No native messaging host dirs for platform")
        return False
    manifest = {
        "name": NATIVE_HOST_IDENTIFIER,
        "description": "Claude Code Browser Extension Native Host",
        "path": manifest_binary_path,
        "type": "stdio",
        "allowed_origins": get_allowed_origins(),
    }
    if not _validate_manifest_structure(manifest):
        log_error_msg("[Claude in Chrome] Generated manifest failed validation")
        return False
    content = json.dumps(manifest, indent=2, ensure_ascii=False)
    any_updated = False
    for d in dirs:
        mp = Path(d) / NATIVE_HOST_MANIFEST_NAME
        try:
            if mp.exists() and mp.read_text("utf-8") == content:
                continue
        except UnicodeDecodeError:
            pass
        try:
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(content, "utf-8")
            any_updated = True
        except OSError as e:
            log_error_msg(f"[Claude in Chrome] Manifest write failed at {mp}: {e}")
    if _platform() in ("windows",) and dirs:
        _register_windows_native_hosts(str(Path(dirs[0]) / NATIVE_HOST_MANIFEST_NAME))
    return any_updated


async def uninstall_chrome_native_host_manifest() -> int:
    """Remove every copy of the native host manifest from all browser dirs.
    Returns the number of manifests removed."""
    dirs = get_native_messaging_hosts_dirs()
    if not dirs:
        return 0
    removed = 0
    for d in dirs:
        mp = Path(d) / NATIVE_HOST_MANIFEST_NAME
        try:
            if mp.exists():
                mp.unlink()
                removed += 1
        except OSError as e:
            log_error_msg(f"[Claude in Chrome] Manifest removal failed at {mp}: {e}")
    if _platform() in ("windows",):
        _unregister_windows_native_hosts()
    return removed


# ---------------------------------------------------------------------------
# Windows registry helpers
# ---------------------------------------------------------------------------

def _windows_browser_keys() -> list[str]:
    """Collect registry key paths for all supported browsers on Windows."""
    keys: list[str] = []
    for b in BROWSER_DETECTION_ORDER:
        rk = CHROMIUM_BROWSERS[b].get("windows", {}).get("registry_key", "")
        if rk:
            keys.append(rk)
    return keys


def _register_windows_native_hosts(manifest_path: str) -> None:
    """Register manifest path under each browser's registry key (Windows only)."""
    if sys.platform != "win32":
        return
    for key in _windows_browser_keys():
        fk = f"{key}\\{NATIVE_HOST_IDENTIFIER}"
        try:
            r = subprocess.run(
                ["reg", "add", fk, "/ve", "/t", "REG_SZ",
                 "/d", manifest_path, "/f"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                log_error_msg(f"[Claude in Chrome] Registry fail {fk}: {r.stderr.strip()}")
        except FileNotFoundError:
            return
        except subprocess.TimeoutExpired:
            log_error_msg(f"[Claude in Chrome] Registry timeout: {fk}")


def _unregister_windows_native_hosts() -> None:
    """Delete registry entries for our native host from all browser keys."""
    if sys.platform != "win32":
        return
    for key in _windows_browser_keys():
        fk = f"{key}\\{NATIVE_HOST_IDENTIFIER}"
        try:
            r = subprocess.run(
                ["reg", "delete", fk, "/f"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode not in (0, 1):
                log_error_msg(f"[Claude in Chrome] Registry delete fail {fk}: {r.stderr.strip()}")
        except FileNotFoundError:
            return
        except subprocess.TimeoutExpired:
            log_error_msg(f"[Claude in Chrome] Registry delete timeout: {fk}")


# ---------------------------------------------------------------------------
# Status inspection
# ---------------------------------------------------------------------------

def get_manifest_status() -> dict:
    """Inspect which browser dirs have a valid manifest installed.
    Returns {browser_dir: "ok" | "missing" | "invalid"}."""
    dirs = get_native_messaging_hosts_dirs()
    status: dict = {}
    for d in dirs:
        mp = Path(d) / NATIVE_HOST_MANIFEST_NAME
        if not mp.exists():
            status[d] = "missing"
            continue
        try:
            data = json.loads(mp.read_text("utf-8"))
            status[d] = "ok" if _validate_manifest_structure(data) else "invalid"
        except (json.JSONDecodeError, OSError):
            status[d] = "invalid"
    return status


# ---------------------------------------------------------------------------
# Extension detection
# ---------------------------------------------------------------------------

async def is_chrome_extension_installed() -> bool:
    """Detect if the extension is installed in any supported Chromium browser."""
    paths = get_all_browser_data_paths()
    if not paths:
        return False
    return await is_chrome_extension_installed_portable(paths)


# ---------------------------------------------------------------------------
# Reconnect page
# ---------------------------------------------------------------------------

async def _open_reconnect_page() -> None:
    """Open the Chrome reconnect URL in the default browser if extension found."""
    if not await is_chrome_extension_installed():
        return
    p = _platform()
    try:
        if p == "macos":
            subprocess.run(["open", CHROME_EXTENSION_RECONNECT_URL],
                           capture_output=True, timeout=10)
        elif p in ("windows",):
            subprocess.run(["rundll32", "url,OpenURL", CHROME_EXTENSION_RECONNECT_URL],
                           capture_output=True, timeout=10)
        else:
            subprocess.run(["xdg-open", CHROME_EXTENSION_RECONNECT_URL],
                           capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------
# Top-level orchestrators
# ---------------------------------------------------------------------------

async def setup_chrome_native_host() -> bool:
    """Setup the Chrome native messaging host for browser extension integration.

    Creates a trampoline script in ~/.hare/chrome/, installs the native host
    manifest JSON into every supported browser's NativeMessagingHosts dir,
    registers with the Windows registry if applicable, and opens the reconnect
    URL on first-time install to guide the user through extension pairing.
    """
    try:
        cmd = f'"{sys.executable}" --chrome-native-host'
        wrapper = await create_wrapper_script(cmd)
        installed = await install_chrome_native_host_manifest(wrapper)
        if installed:
            await _open_reconnect_page()
        return installed
    except Exception as e:
        log_error_msg(f"[Claude in Chrome] Setup failed: {e}")
        return False


async def teardown_chrome_native_host() -> bool:
    """Remove the Chrome native host manifest and wrapper script.
    Returns True if cleanup was successful (or nothing to clean)."""
    try:
        await uninstall_chrome_native_host_manifest()
        chrome_dir = Path(get_hare_config_home_dir()) / "chrome"
        for name in ("chrome-native-host", "chrome-native-host.bat", "enabled"):
            candidate = chrome_dir / name
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass
        return True
    except Exception as e:
        log_error_msg(f"[Claude in Chrome] Teardown failed: {e}")
        return False
