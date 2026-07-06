"""IDE detection, lockfiles, extension install — port of `ide.ts`."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import socket
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from hare.utils.abort_controller import create_abort_controller
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.errors import error_message, is_fs_inaccessible
from hare.utils.exec_file_no_throw import exec_file_no_throw, exec_file_no_throw_with_cwd
from hare.utils.exec_file_no_throw_portable import exec_sync_with_defaults_deprecated
from hare.utils.fs_operations import get_fs_implementation
from hare.utils.generic_process_utils import get_ancestor_pids_async
from hare.utils.jetbrains import is_jetbrains_plugin_installed_cached
from hare.utils.log import log_error
from hare.utils.platform import Platform, get_platform
from hare.utils.semver import lt as semver_lt
from hare.utils.sleep import sleep as async_sleep
from hare.utils.slow_operations import json_parse as _safe_json_parse

# ---------------------------------------------------------------------------
# Re-exported from companion modules
# ---------------------------------------------------------------------------
from hare.utils.ide_path_conversion import WindowsToWSLConverter, check_wsl_distro_match  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CC_VERSION = "2.1.88"

EXTENSION_ID = (
    "anthropic.claude-code-internal"
    if os.environ.get("USER_TYPE") == "ant"
    else "anthropic.claude-code"
)

EDITOR_DISPLAY_NAMES: dict[str, str] = {
    "code": "VS Code",
    "cursor": "Cursor",
    "windsurf": "Windsurf",
    "antigravity": "Antigravity",
    "vi": "Vim",
    "vim": "Vim",
    "nano": "nano",
    "notepad": "Notepad",
    "start /wait notepad": "Notepad",
    "emacs": "Emacs",
    "subl": "Sublime Text",
    "atom": "Atom",
}

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

IdeType = Literal[
    "cursor",
    "windsurf",
    "vscode",
    "pycharm",
    "intellij",
    "webstorm",
    "phpstorm",
    "rubymine",
    "clion",
    "goland",
    "rider",
    "datagrip",
    "appcode",
    "dataspell",
    "aqua",
    "gateway",
    "fleet",
    "androidstudio",
]


@dataclass
class IdeConfig:
    ide_kind: Literal["vscode", "jetbrains"]
    display_name: str
    process_keywords_mac: list[str]
    process_keywords_windows: list[str]
    process_keywords_linux: list[str]


SUPPORTED_IDE_CONFIGS: dict[str, IdeConfig] = {
    "cursor": IdeConfig(
        "vscode", "Cursor", ["Cursor Helper", "Cursor.app"], ["cursor.exe"], ["cursor"]
    ),
    "windsurf": IdeConfig(
        "vscode",
        "Windsurf",
        ["Windsurf Helper", "Windsurf.app"],
        ["windsurf.exe"],
        ["windsurf"],
    ),
    "vscode": IdeConfig(
        "vscode",
        "VS Code",
        ["Visual Studio Code", "Code Helper"],
        ["code.exe"],
        ["code"],
    ),
    "intellij": IdeConfig(
        "jetbrains",
        "IntelliJ IDEA",
        ["IntelliJ IDEA"],
        ["idea64.exe"],
        ["idea", "intellij"],
    ),
    "pycharm": IdeConfig(
        "jetbrains", "PyCharm", ["PyCharm"], ["pycharm64.exe"], ["pycharm"]
    ),
    "webstorm": IdeConfig(
        "jetbrains", "WebStorm", ["WebStorm"], ["webstorm64.exe"], ["webstorm"]
    ),
    "phpstorm": IdeConfig(
        "jetbrains", "PhpStorm", ["PhpStorm"], ["phpstorm64.exe"], ["phpstorm"]
    ),
    "rubymine": IdeConfig(
        "jetbrains", "RubyMine", ["RubyMine"], ["rubymine64.exe"], ["rubymine"]
    ),
    "clion": IdeConfig("jetbrains", "CLion", ["CLion"], ["clion64.exe"], ["clion"]),
    "goland": IdeConfig(
        "jetbrains", "GoLand", ["GoLand"], ["goland64.exe"], ["goland"]
    ),
    "rider": IdeConfig("jetbrains", "Rider", ["Rider"], ["rider64.exe"], ["rider"]),
    "datagrip": IdeConfig(
        "jetbrains", "DataGrip", ["DataGrip"], ["datagrip64.exe"], ["datagrip"]
    ),
    "appcode": IdeConfig(
        "jetbrains", "AppCode", ["AppCode"], ["appcode.exe"], ["appcode"]
    ),
    "dataspell": IdeConfig(
        "jetbrains", "DataSpell", ["DataSpell"], ["dataspell64.exe"], ["dataspell"]
    ),
    "aqua": IdeConfig("jetbrains", "Aqua", [], ["aqua64.exe"], []),
    "gateway": IdeConfig("jetbrains", "Gateway", [], ["gateway64.exe"], []),
    "fleet": IdeConfig("jetbrains", "Fleet", [], ["fleet.exe"], []),
    "androidstudio": IdeConfig(
        "jetbrains",
        "Android Studio",
        ["Android Studio"],
        ["studio64.exe"],
        ["android-studio"],
    ),
}


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _LockfileJsonContent:
    workspace_folders: list[str] | None = None
    pid: int | None = None
    ide_name: str | None = None
    transport: str | None = None  # 'ws' | 'sse'
    running_in_windows: bool | None = None
    auth_token: str | None = None


@dataclass
class _IdeLockfileInfo:
    workspace_folders: list[str]
    port: int
    pid: int | None = None
    ide_name: str | None = None
    use_web_socket: bool = False
    running_in_windows: bool = False
    auth_token: str | None = None


@dataclass
class DetectedIdeInfo:
    name: str
    port: int
    workspace_folders: list[str]
    url: str
    is_valid: bool
    auth_token: str | None = None
    ide_running_in_windows: bool | None = None


@dataclass
class IdeExtensionInstallationStatus:
    installed: bool
    error: str | None = None
    installed_version: str | None = None
    ide_type: str | None = None


# ---------------------------------------------------------------------------
# IDE kind helpers
# ---------------------------------------------------------------------------


def is_vscode_ide(ide: str | None) -> bool:
    if not ide:
        return False
    c = SUPPORTED_IDE_CONFIGS.get(ide)
    return c.ide_kind == "vscode" if c else False


def is_jetbrains_ide(ide: str | None) -> bool:
    if not ide:
        return False
    c = SUPPORTED_IDE_CONFIGS.get(ide)
    return c.ide_kind == "jetbrains" if c else False


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def _is_process_running(pid: int) -> bool:
    """Check if a process is alive via os.kill(pid, 0)."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _make_ancestor_pid_lookup() -> Callable[[], "asyncio.Future[set[int]]"]:
    """Return a callable that lazily fetches and caches ancestor PIDs.

    PIDs recycle and process trees change over time, so scope this to a single
    detection pass.
    """
    promise: asyncio.Future[set[int]] | None = None

    async def _lookup() -> set[int]:
        nonlocal promise
        # The first caller creates the future; subsequent callers await it.
        loop = asyncio.get_running_loop()
        if promise is None:
            promise = loop.create_future()
            try:
                ppid = os.getppid()
                pids = await get_ancestor_pids_async(ppid, 10)
                if not promise.done():
                    promise.set_result(set(pids))
            except Exception as exc:
                if not promise.done():
                    promise.set_exception(exc)
            return await promise
        return await promise

    return _lookup


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


async def _check_ide_connection(
    host: str,
    port: int,
    timeout: float = 0.5,
) -> bool:
    """Check if a TCP port is open (IDE is reachable)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError, Exception):
        return False


async def _detect_host_ip(
    is_ide_running_in_windows: bool,
    port: int,
) -> str:
    """Determine the host IP to use for the IDE extension connection."""
    override = os.environ.get("CLAUDE_CODE_IDE_HOST_OVERRIDE")
    if override:
        return override

    plat = get_platform()
    if plat != "wsl" or not is_ide_running_in_windows:
        return "127.0.0.1"

    # WSL2: extension runs in Windows, so use the WSL gateway IP.
    try:
        proc = await asyncio.create_subprocess_shell(
            "ip route show | grep -i default",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            route_text = stdout.decode(errors="replace")
            m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", route_text)
            if m:
                gateway_ip = m.group(1)
                if await _check_ide_connection(gateway_ip, port):
                    return gateway_ip
    except (asyncio.TimeoutError, OSError):
        pass

    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Lockfile paths / I/O
# ---------------------------------------------------------------------------


_windows_user_profile_cache: str | None = None
_profile_cache_lock = asyncio.Lock()


async def _get_windows_user_profile() -> str | None:
    """Get the Windows USERPROFILE, shelling out to PowerShell if needed.

    Cached per-process since USERPROFILE is static; the PowerShell spawn is
    ~500ms-2s cold.
    """
    global _windows_user_profile_cache
    profile = os.environ.get("USERPROFILE")
    if profile:
        return profile
    async with _profile_cache_lock:
        if _windows_user_profile_cache is not None:
            return _windows_user_profile_cache or None
        result = await exec_file_no_throw("powershell.exe", [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$env:USERPROFILE",
        ])
        if result["code"] == 0 and (result["stdout"] or "").strip():
            _windows_user_profile_cache = result["stdout"].strip()
            return _windows_user_profile_cache
        log_for_debugging(
            "Unable to get Windows USERPROFILE via PowerShell - IDE detection may be incomplete"
        )
        _windows_user_profile_cache = ""
        return None


async def _get_ide_lockfiles_paths() -> list[str]:
    """Return potential IDE lockfile directories (not pre-checked for existence)."""
    paths: list[str] = [
        os.path.join(get_hare_config_home_dir(), "ide"),
    ]

    plat = get_platform()
    if plat != "wsl":
        return paths

    # WSL: also scan Windows-side .claude/ide directories.
    windows_home = await _get_windows_user_profile()
    if windows_home:
        converter = WindowsToWSLConverter(os.environ.get("WSL_DISTRO_NAME"))
        wsl_path = converter.to_local_path(windows_home)
        paths.append(os.path.join(wsl_path, ".claude", "ide"))

    try:
        users_dir = "/mnt/c/Users"
        fs = get_fs_implementation()
        entries = []
        try:
            # os.scandir for performance
            with os.scandir(users_dir) as it:
                entries = list(it)
        except OSError:
            return paths

        skip_dirs = {"Public", "Default", "Default User", "All Users"}
        for entry in entries:
            if entry.name in skip_dirs:
                continue
            if not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                continue
            paths.append(os.path.join(users_dir, entry.name, ".claude", "ide"))
    except OSError as exc:
        if not is_fs_inaccessible(exc):
            log_error(exc)

    return paths


async def _read_ide_lockfile(path: str) -> _IdeLockfileInfo | None:
    """Parse a single .lock file into structured info."""
    try:
        fs = get_fs_implementation()
        content = fs.read_file_sync(path)
    except OSError as exc:
        log_error(exc)
        return None

    workspace_folders: list[str] = []
    pid: int | None = None
    ide_name: str | None = None
    use_web_socket = False
    running_in_windows = False
    auth_token: str | None = None

    try:
        parsed = _safe_json_parse(content)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("workspaceFolders"), list):
                workspace_folders = [str(p) for p in parsed["workspaceFolders"]]
            pid_raw = parsed.get("pid")
            if isinstance(pid_raw, int):
                pid = pid_raw
            ide_name = str(parsed["ideName"]) if isinstance(parsed.get("ideName"), str) else None
            use_web_socket = parsed.get("transport") == "ws"
            running_in_windows = parsed.get("runningInWindows") is True
            auth_token = (
                str(parsed["authToken"])
                if isinstance(parsed.get("authToken"), str)
                else None
            )
    except (json.JSONDecodeError, Exception):
        # Older format: plain list of directories, one per line
        workspace_folders = [line.strip() for line in content.split("\n") if line.strip()]

    # Extract port from filename (e.g., "12345.lock" -> 12345)
    filename = os.path.basename(path)
    port_str = filename.replace(".lock", "")
    try:
        port = int(port_str)
    except ValueError:
        return None

    return _IdeLockfileInfo(
        workspace_folders=workspace_folders,
        port=port,
        pid=pid,
        ide_name=ide_name,
        use_web_socket=use_web_socket,
        running_in_windows=running_in_windows,
        auth_token=auth_token,
    )


# ---------------------------------------------------------------------------
# IDE running detection
# ---------------------------------------------------------------------------


async def _detect_running_ides_impl() -> list[str]:
    """Internal: detect running IDEs by scanning process list."""
    running: list[str] = []
    plat = get_platform()

    try:
        if plat == "macos":
            result = await asyncio.create_subprocess_shell(
                'ps aux | grep -E "Visual Studio Code|Code Helper|Cursor Helper|'
                'Windsurf Helper|IntelliJ IDEA|PyCharm|WebStorm|PhpStorm|RubyMine|'
                'CLion|GoLand|Rider|DataGrip|AppCode|DataSpell|Aqua|Gateway|Fleet|'
                'Android Studio" | grep -v grep',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10.0)
            stdout_text = stdout.decode(errors="replace")
            for ide_name, config in SUPPORTED_IDE_CONFIGS.items():
                for kw in config.process_keywords_mac:
                    if kw and kw in stdout_text:
                        running.append(ide_name)
                        break

        elif plat == "windows":
            result = await asyncio.create_subprocess_shell(
                'tasklist | findstr /I "Code.exe Cursor.exe Windsurf.exe idea64.exe '
                'pycharm64.exe webstorm64.exe phpstorm64.exe rubymine64.exe clion64.exe '
                'goland64.exe rider64.exe datagrip64.exe appcode.exe dataspell64.exe '
                'aqua64.exe gateway64.exe fleet.exe studio64.exe"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10.0)
            stdout_lower = stdout.decode(errors="replace").lower()
            for ide_name, config in SUPPORTED_IDE_CONFIGS.items():
                for kw in config.process_keywords_windows:
                    if kw and kw.lower() in stdout_lower:
                        running.append(ide_name)
                        break

        else:  # linux or wsl
            result = await asyncio.create_subprocess_shell(
                'ps aux | grep -E "code|cursor|windsurf|idea|pycharm|webstorm|'
                'phpstorm|rubymine|clion|goland|rider|datagrip|dataspell|aqua|'
                'gateway|fleet|android-studio" | grep -v grep',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10.0)
            stdout_lower = stdout.decode(errors="replace").lower()
            for ide_name, config in SUPPORTED_IDE_CONFIGS.items():
                for kw in config.process_keywords_linux:
                    if not kw:
                        continue
                    if kw in stdout_lower:
                        if ide_name != "vscode":
                            running.append(ide_name)
                            break
                        # vscode: avoid false matches from cursor/appcode process names
                        if "cursor" not in stdout_lower and "appcode" not in stdout_lower:
                            running.append(ide_name)
                            break
    except (asyncio.TimeoutError, OSError) as exc:
        log_error(exc)

    return running


# Cache for detect_running_ides_cached()
_cached_running_ides: list[str] | None = None


async def detect_running_ides() -> list[str]:
    """Detect running IDEs and update the cache."""
    global _cached_running_ides
    result = await _detect_running_ides_impl()
    _cached_running_ides = list(result)
    return result


async def detect_running_ides_cached() -> list[str]:
    """Return cached running IDE list, or perform detection if cache empty."""
    global _cached_running_ides
    if _cached_running_ides is None:
        return await detect_running_ides()
    return list(_cached_running_ides)


def reset_detect_running_ides() -> None:
    """Reset the running-IDE detection cache (for testing)."""
    global _cached_running_ides
    _cached_running_ides = None


# ---------------------------------------------------------------------------
# Lockfile management
# ---------------------------------------------------------------------------


async def get_sorted_ide_lockfiles() -> list[str]:
    """Return ``.lock`` paths from IDE lockfile directories, newest first."""
    try:
        ide_lock_paths = await _get_ide_lockfiles_paths()
        all_lockfiles: list[tuple[str, float]] = []

        for lock_dir in ide_lock_paths:
            try:
                with os.scandir(lock_dir) as it:
                    entries = list(it)
            except OSError:
                # Directory may not exist or be inaccessible; skip
                continue

            for entry in entries:
                if not entry.name.endswith(".lock"):
                    continue
                full_path = os.path.join(lock_dir, entry.name)
                try:
                    mtime = entry.stat().st_mtime
                    all_lockfiles.append((full_path, mtime))
                except OSError:
                    continue

        # Sort by modification time descending (newest first)
        all_lockfiles.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in all_lockfiles]
    except Exception as exc:
        log_error(exc)
        return []


async def cleanup_stale_ide_lockfiles() -> None:
    """Remove lockfiles for dead processes or unresponsive ports."""
    try:
        lockfiles = await get_sorted_ide_lockfiles()
        for lock_path in lockfiles:
            info = await _read_ide_lockfile(lock_path)
            if info is None:
                # Unreadable lockfile — remove it
                try:
                    await asyncio.to_thread(os.unlink, lock_path)
                except OSError:
                    pass
                continue

            host = await _detect_host_ip(info.running_in_windows, info.port)
            should_delete = False

            if info.pid is not None:
                if not _is_process_running(info.pid):
                    if get_platform() != "wsl":
                        should_delete = True
                    else:
                        # PID may be unreliable in WSL; also check connection
                        is_responding = await _check_ide_connection(host, info.port)
                        if not is_responding:
                            should_delete = True
            else:
                # No PID — fall back to connection check
                is_responding = await _check_ide_connection(host, info.port)
                if not is_responding:
                    should_delete = True

            if should_delete:
                try:
                    await asyncio.to_thread(os.unlink, lock_path)
                except OSError:
                    pass
    except Exception as exc:
        log_error(exc)


# ---------------------------------------------------------------------------
# IDE detection
# ---------------------------------------------------------------------------

# Current in-flight IDE search (for cancellation)
_current_ide_search: Any = None


async def detect_ides(include_invalid: bool) -> list[DetectedIdeInfo]:
    """Detect IDEs with a running extension/plugin via lockfiles."""
    detected: list[DetectedIdeInfo] = []

    try:
        sse_port_raw = os.environ.get("CLAUDE_CODE_SSE_PORT")
        env_port = int(sse_port_raw) if sse_port_raw else None

        # Normalize CWD to NFC for consistent comparison (macOS returns NFD paths)
        cwd = os.getcwd()
        try:
            cwd = unicodedata.normalize("NFC", os.path.realpath(cwd))
        except OSError:
            cwd = unicodedata.normalize("NFC", cwd)

        lockfiles = await get_sorted_ide_lockfiles()
        lockfile_infos = await asyncio.gather(
            *[_read_ide_lockfile(lf) for lf in lockfiles],
            return_exceptions=True,
        )

        get_ancestors = _make_ancestor_pid_lookup()
        plat = get_platform()
        supported_terminal = bool(
            is_vscode_ide(os.environ.get("CLAUDECODE_IDE", ""))
            or is_jetbrains_ide(os.environ.get("CLAUDECODE_IDE", ""))
            or is_env_truthy(os.environ.get("FORCE_CODE_TERMINAL"))
        )
        needs_ancestry_check = plat != "wsl" and supported_terminal

        for result in lockfile_infos:
            if isinstance(result, BaseException):
                continue
            if result is None:
                continue

            info: _IdeLockfileInfo = result

            # --- validity check ---
            is_valid = False
            if is_env_truthy(os.environ.get("CLAUDE_CODE_IDE_SKIP_VALID_CHECK")):
                is_valid = True
            elif info.port == env_port:
                is_valid = True
            else:
                for ide_path in info.workspace_folders:
                    if not ide_path:
                        continue
                    local_path = ide_path

                    # WSL path conversion
                    if (
                        plat == "wsl"
                        and info.running_in_windows
                        and os.environ.get("WSL_DISTRO_NAME")
                    ):
                        if not check_wsl_distro_match(ide_path, os.environ["WSL_DISTRO_NAME"]):
                            continue

                        resolved_original = unicodedata.normalize(
                            "NFC", os.path.realpath(local_path)
                        )
                        if (
                            cwd == resolved_original
                            or cwd.startswith(resolved_original + os.sep)
                        ):
                            is_valid = True
                            break

                        converter = WindowsToWSLConverter(os.environ["WSL_DISTRO_NAME"])
                        local_path = converter.to_local_path(ide_path)

                    resolved_path = unicodedata.normalize("NFC", os.path.realpath(local_path))

                    # Windows: case-insensitive drive letter
                    if plat == "windows":
                        normalized_cwd = re.sub(
                            r"^[a-zA-Z]:", lambda m: m.group(0).upper(), cwd
                        )
                        normalized_path = re.sub(
                            r"^[a-zA-Z]:", lambda m: m.group(0).upper(), resolved_path
                        )
                        if (
                            normalized_cwd == normalized_path
                            or normalized_cwd.startswith(normalized_path + os.sep)
                        ):
                            is_valid = True
                            break
                    else:
                        if cwd == resolved_path or cwd.startswith(resolved_path + os.sep):
                            is_valid = True
                            break

            if not is_valid and not include_invalid:
                continue

            # --- ancestry check ---
            if needs_ancestry_check:
                port_matches_env = env_port is not None and info.port == env_port
                if not port_matches_env:
                    if info.pid is None or not _is_process_running(info.pid):
                        continue
                    if os.getppid() != info.pid:
                        ancestors = await get_ancestors()
                        if info.pid not in ancestors:
                            continue

            # --- build result ---
            ide_name = info.ide_name or (
                to_ide_display_name(os.environ.get("CLAUDECODE_IDE"))
                if supported_terminal
                else "IDE"
            )

            host = await _detect_host_ip(info.running_in_windows, info.port)
            if info.use_web_socket:
                url = f"ws://{host}:{info.port}"
            else:
                url = f"http://{host}:{info.port}/sse"

            detected.append(
                DetectedIdeInfo(
                    url=url,
                    name=ide_name,
                    workspace_folders=list(info.workspace_folders),
                    port=info.port,
                    is_valid=is_valid,
                    auth_token=info.auth_token,
                    ide_running_in_windows=info.running_in_windows,
                )
            )

        # If envPort is set and we have exactly one match, narrow to it.
        if not include_invalid and env_port is not None:
            env_matches = [d for d in detected if d.is_valid and d.port == env_port]
            if len(env_matches) == 1:
                return env_matches

    except Exception as exc:
        log_error(exc)

    return detected


async def find_available_ide() -> DetectedIdeInfo | None:
    """Poll for a connected IDE for up to 30s. Returns the single IDE or None."""
    global _current_ide_search

    # Abort any previous search
    if _current_ide_search is not None:
        ctrl = _current_ide_search
        if hasattr(ctrl, "abort"):
            ctrl.abort()
    controller = create_abort_controller()
    _current_ide_search = controller
    signal = controller.signal

    try:
        await cleanup_stale_ide_lockfiles()
        import time

        start = time.time()
        while time.time() - start < 30.0 and not signal.aborted:
            ides = await detect_ides(include_invalid=False)
            if signal.aborted:
                return None
            if len(ides) == 1:
                return ides[0]
            await async_sleep(1000)  # 1 second

        return None
    finally:
        if _current_ide_search is controller:
            _current_ide_search = None


# ---------------------------------------------------------------------------
# IDE command discovery (VS Code variants)
# ---------------------------------------------------------------------------


def _get_vscode_ide_command_by_parent_process() -> str | None:
    """Walk the parent-process tree on macOS to find the VS Code variant binary."""
    plat = get_platform()
    if plat != "macos":
        return None

    try:
        pid: int | None = os.getppid()
        for _ in range(10):  # noqa: B007
            if pid is None or pid <= 1:
                break

            cmd = exec_sync_with_defaults_deprecated(f"ps -o command= -p {pid}")
            if cmd:
                app_names = {
                    "Visual Studio Code.app": "code",
                    "Cursor.app": "cursor",
                    "Windsurf.app": "windsurf",
                    "Visual Studio Code - Insiders.app": "code",
                    "VSCodium.app": "codium",
                }
                path_to_exec = "/Contents/MacOS/Electron"
                for app_name, exe_name in app_names.items():
                    idx = cmd.find(app_name + path_to_exec)
                    if idx != -1:
                        folder_end = idx + len(app_name)
                        return (
                            cmd[:folder_end]
                            + "/Contents/Resources/app/bin/"
                            + exe_name
                        )

            ppid_str = exec_sync_with_defaults_deprecated(f"ps -o ppid= -p {pid}")
            if not ppid_str:
                break
            try:
                pid = int(ppid_str.strip())
            except ValueError:
                break

    except OSError:
        pass

    return None


async def _get_vscode_ide_command(ide_type: str) -> str | None:
    """Resolve the CLI command for a VS Code-family IDE."""
    # macOS: try to find the exact binary via parent process
    parent_exe = _get_vscode_ide_command_by_parent_process()
    if parent_exe:
        try:
            fs = get_fs_implementation()
            fs.stat_sync(parent_exe)
            return parent_exe
        except OSError:
            pass

    # Windows: prefer .cmd to avoid launching the GUI binary
    ext = ".cmd" if get_platform() == "windows" else ""
    mapping = {
        "vscode": "code",
        "cursor": "cursor",
        "windsurf": "windsurf",
    }
    cmd = mapping.get(ide_type)
    if cmd:
        return cmd + ext
    return None


def _get_installation_env() -> dict[str, str] | None:
    """Return modified env for VS Code extension installation.

    On Linux, clear DISPLAY to prevent GUI launch edge-case.
    """
    if get_platform() == "linux":
        env = dict(os.environ)
        env["DISPLAY"] = ""
        return env
    return None


# ---------------------------------------------------------------------------
# Display name helper
# ---------------------------------------------------------------------------


def to_ide_display_name(terminal: str | None) -> str:
    """Convert an IDE type or editor command to a display name."""
    if not terminal:
        return "IDE"

    c = SUPPORTED_IDE_CONFIGS.get(terminal)
    if c:
        return c.display_name

    # Check exact editor name match
    cleaned = terminal.lower().strip()
    editor_name = EDITOR_DISPLAY_NAMES.get(cleaned)
    if editor_name:
        return editor_name

    # Extract command from path/args: "/usr/bin/code --wait" -> "code"
    command = terminal.split()[0]
    command_name = os.path.basename(command).lower() if command else None
    if command_name:
        mapped = EDITOR_DISPLAY_NAMES.get(command_name)
        if mapped:
            return mapped
        return command_name.capitalize()

    return terminal.capitalize()


# ---------------------------------------------------------------------------
# IDE extension installation
# ---------------------------------------------------------------------------


async def _get_installed_vscode_extension_version(command: str) -> str | None:
    """Query the installed version of the Claude Code VS Code extension."""
    result = await exec_file_no_throw(command, [
        "--list-extensions",
        "--show-versions",
    ])
    if result["code"] != 0:
        return None
    stdout = result["stdout"] or ""
    for line in stdout.split("\n"):
        parts = line.split("@")
        if len(parts) >= 2 and parts[0] == "anthropic.claude-code":
            return parts[1].strip() or None
    return None


async def _install_ide_extension(ide_type: str) -> str | None:
    """Install/update the IDE extension and return the version string."""
    if not is_vscode_ide(ide_type):
        return None  # JetBrains auto-install not supported

    command = await _get_vscode_ide_command(ide_type)
    if not command:
        return None

    version = await _get_installed_vscode_extension_version(command)
    if not version or semver_lt(version, CC_VERSION):
        await async_sleep(500)  # Avoid rapid-fire code commands that may crash
        result = await exec_file_no_throw_with_cwd(
            command,
            ["--force", "--install-extension", "anthropic.claude-code"],
            env=_get_installation_env(),
            timeout=60000,
        )
        if result["code"] != 0:
            err = result.get("error") or result.get("stderr") or "unknown"
            raise RuntimeError(f"Extension install failed: {err}")
        return CC_VERSION

    return version


async def is_ide_extension_installed(ide_type: str) -> bool:
    """Check whether the Claude Code extension/plugin is installed for an IDE."""
    if is_vscode_ide(ide_type):
        command = await _get_vscode_ide_command(ide_type)
        if not command:
            return False
        try:
            result = await exec_file_no_throw_with_cwd(
                command,
                ["--list-extensions"],
                env=_get_installation_env(),
                timeout=30000,
            )
            if result["code"] == 0 and EXTENSION_ID in (result["stdout"] or ""):
                return True
        except OSError:
            pass
        return False

    if is_jetbrains_ide(ide_type):
        return await is_jetbrains_plugin_installed_cached(ide_type)

    return False


async def maybe_install_ide_extension(
    ide_type: str,
) -> IdeExtensionInstallationStatus | None:
    """Install the IDE extension and return installation status."""
    try:
        installed_version = await _install_ide_extension(ide_type)
        return IdeExtensionInstallationStatus(
            installed=True,
            installed_version=installed_version,
            ide_type=ide_type,
        )
    except Exception as exc:
        log_error(exc)
        msg = str(exc) if isinstance(exc, (RuntimeError, OSError)) else "Installation failed"
        return IdeExtensionInstallationStatus(
            installed=False,
            error=msg,
            installed_version=None,
            ide_type=ide_type,
        )


# ---------------------------------------------------------------------------
# Installed checks (VS Code variants)
# ---------------------------------------------------------------------------


async def is_vscode_installed() -> bool:
    """Check if VS Code is installed and accessible."""
    result = await exec_file_no_throw("code", ["--help"])
    return result["code"] == 0 and "Visual Studio Code" in (result["stdout"] or "")


async def is_cursor_installed() -> bool:
    """Check if Cursor is installed and accessible."""
    result = await exec_file_no_throw("cursor", ["--version"])
    return result["code"] == 0


async def is_windsurf_installed() -> bool:
    """Check if Windsurf is installed and accessible."""
    result = await exec_file_no_throw("windsurf", ["--version"])
    return result["code"] == 0


# ---------------------------------------------------------------------------
# Connected IDE client helpers
# ---------------------------------------------------------------------------


def get_connected_ide_client(
    mcp_clients: list[Any] | None,
) -> Any:
    """Return the connected IDE client from a list of MCP clients, or None."""
    if not mcp_clients:
        return None

    for client in mcp_clients:
        if (
            getattr(client, "type", None) == "connected"
            and getattr(client, "name", None) == "ide"
        ):
            return client

    return None


def get_connected_ide_name(mcp_clients: list[Any] | None) -> str | None:
    """Get the display name of the connected IDE client."""
    if not mcp_clients:
        return None

    ide_client = get_connected_ide_client(mcp_clients)
    return _get_ide_client_name(ide_client)


def _get_ide_client_name(ide_client: Any) -> str | None:
    """Extract the display name from an IDE client's config."""
    if ide_client is None:
        return None

    config = getattr(ide_client, "config", None)
    if config is not None:
        config_type = getattr(config, "type", None)
        if config_type in ("sse-ide", "ws-ide"):
            return getattr(config, "ideName", None)

    # Fallback: detect from terminal
    terminal = os.environ.get("CLAUDECODE_IDE")
    if terminal:
        return to_ide_display_name(terminal)

    return None


def has_access_to_ide_extension_diff_feature(mcp_clients: list[Any]) -> bool:
    """Check if any connected IDE client provides diff functionality."""
    return any(
        getattr(c, "name", None) == "ide"
        for c in mcp_clients
        if getattr(c, "type", None) == "connected"
    )


# ---------------------------------------------------------------------------
# IDE notification
# ---------------------------------------------------------------------------


async def maybe_notify_ide_connected(client: Any) -> None:
    """Notify the IDE that this CLI process has connected."""
    try:
        notify = getattr(client, "notification", None)
        if callable(notify):
            await notify({
                "method": "ide_connected",
                "params": {
                    "pid": os.getpid(),
                },
            })
    except Exception as exc:
        log_error(exc)


# ---------------------------------------------------------------------------
# IDE RPC helpers
# ---------------------------------------------------------------------------


async def close_open_diffs(ide_client: Any) -> None:
    """Notify the IDE to close all open diff tabs."""
    try:
        call_rpc = getattr(ide_client, "call_ide_rpc", None)
        if callable(call_rpc):
            await call_rpc("closeAllDiffTabs", {})
    except Exception:
        # Silently ignore errors — best-effort operation
        pass
