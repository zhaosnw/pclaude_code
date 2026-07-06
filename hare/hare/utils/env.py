"""
Process and runtime environment probes for analytics and UX.

Port of: src/utils/env.ts

Provides comprehensive runtime-environment detection including:
- Platform, architecture, and Python version
- Terminal emulator identification
- Deployment/hosting environment detection (CI, cloud, containers)
- Package manager and runtime availability
- Network connectivity and SSH session checks
- WSL, IDE, and container detection
- OAuth configuration path resolution
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Tuple, runtime_checkable

import urllib.request

from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.find_executable import find_executable


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PlatformName = Literal["win32", "darwin", "linux"]

# Friendly names used in analytics / user-facing strings
_PLATFORM_FRIENDLY: Dict[PlatformName, str] = {
    "win32": "Windows",
    "darwin": "macOS",
    "linux": "Linux",
}

ContainerEngine = Literal["docker", "podman", "lxc", "containerd", "unknown-container"]


# ---------------------------------------------------------------------------
# OAuth configuration suffix
# ---------------------------------------------------------------------------

def _get_oauth_config_type() -> str:
    """
    Determine the OAuth environment type.

    Returns one of 'local', 'staging', or 'prod'.  Only `ant` users can
    switch to local or staging via env vars.
    """
    if os.environ.get("USER_TYPE") == "ant":
        if is_env_truthy(os.environ.get("USE_LOCAL_OAUTH")):
            return "local"
        if is_env_truthy(os.environ.get("USE_STAGING_OAUTH")):
            return "staging"
    return "prod"


def _file_suffix_for_oauth_config() -> str:
    """
    Return the OAuth filename suffix based on the current configuration.

    - ``CLAUDE_CODE_CUSTOM_OAUTH_URL``  →  ``-custom-oauth``
    - local OAuth (ant user)            →  ``-local-oauth``
    - staging OAuth (ant user)          →  ``-staging-oauth``
    - production                         →  ``""``
    - ``CLAUDE_OAUTH_CONFIG_SUFFIX`` env var always takes precedence.
    """
    # Explicit override always wins
    override = os.environ.get("CLAUDE_OAUTH_CONFIG_SUFFIX")
    if override is not None:
        return override

    if os.environ.get("CLAUDE_CODE_CUSTOM_OAUTH_URL"):
        return "-custom-oauth"

    config_type = _get_oauth_config_type()
    if config_type == "local":
        return "-local-oauth"
    if config_type == "staging":
        return "-staging-oauth"
    # production
    return ""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_fs_implementation():
    """Lazy import to avoid circular imports at module level."""
    from hare.utils.fs_operations import get_fs_implementation as _gfi

    return _gfi()


def get_home_directory() -> str:
    """
    Return the user's home directory.

    Precedence: ``HOME`` / ``USERPROFILE`` (Windows) → ``~`` expansion → current dir fallback.
    """
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        return str(Path(home))
    try:
        return str(Path.home())
    except RuntimeError:
        return str(Path.cwd())


def get_temp_directory() -> str:
    """
    Return a consistent temporary directory.

    Checks ``TMPDIR``, ``TEMP``, ``TMP``, then platform-specific fallbacks.
    """
    for var in ("TMPDIR", "TEMP", "TMP"):
        val = os.environ.get(var)
        if val:
            return str(Path(val))

    plat = _normalized_platform()
    if plat == "win32":
        candidates = [r"C:\Temp", r"C:\Windows\Temp"]
        for c in candidates:
            if Path(c).is_dir():
                return c
    return "/tmp"


@lru_cache(maxsize=1)
def get_global_hare_file() -> str:
    """
    Return the path to the global hare configuration file.

    Legacy-fallback logic (mirrors TS):
    1. If ``HARE_CONFIG_DIR`` is set, resolve ``<HARE_CONFIG_DIR>/.hare<oauth>.json``.
    2. Else use the computed config home directory (``get_hare_config_home_dir()``).
    3. Before settling on the hare filename, check for a legacy ``.config.json``
       in the config home (backwards compatibility).
    4. Also check for a legacy ``.claude<oauth>.json`` in ``CLAUDE_CONFIG_DIR``
       or ``~/.claude<oauth>.json``.
    5. Fall back to ``<config_home>/.hare<oauth>.json``.
    """
    fs = _get_fs_implementation()
    oauth_suffix = _file_suffix_for_oauth_config()

    config_home = get_hare_config_home_dir()

    # Legacy: ~/<config_home>/.config.json
    legacy_config_json = str(Path(config_home) / ".config.json")
    if fs.exists_sync(legacy_config_json):
        return legacy_config_json

    hare_filename = f".hare{oauth_suffix}.json"
    legacy_claude_filename = f".claude{oauth_suffix}.json"

    # Legacy: CLAUDE_CONFIG_DIR or ~/.claude<oauth>.json
    claude_config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or get_home_directory()
    legacy_claude_path = str(Path(claude_config_dir) / legacy_claude_filename)
    if fs.exists_sync(legacy_claude_path):
        return legacy_claude_path

    # Default
    return str(Path(config_home) / hare_filename)


# ---------------------------------------------------------------------------
# Network / connectivity
# ---------------------------------------------------------------------------

class _NetworkProbeError(Exception):
    """Raised when the network probe fails for a known reason."""


@lru_cache(maxsize=1)
def _has_internet_access_cached() -> bool:
    """
    Probe internet access with a HEAD request to 1.1.1.1.

    Uses a short timeout (1 s) and catches specific error classes so we
    can log / report distinct failure modes internally.
    """
    url = "http://1.1.1.1"
    try:
        req = urllib.request.Request(url, method="HEAD")
        # Python's urllib doesn't expose an easy AbortSignal.timeout, so we
        # set the socket timeout on the global default.
        with urllib.request.urlopen(req, timeout=1.0) as r:
            return r.status < 500
    except urllib.error.URLError as exc:
        # Distinguish DNS / connectivity vs HTTP errors
        if isinstance(exc.reason, socket.gaierror):
            # DNS resolution failed
            _record_network_failure("dns", str(exc.reason))
        elif isinstance(exc.reason, socket.timeout):
            _record_network_failure("timeout", str(exc.reason))
        elif isinstance(exc.reason, ConnectionRefusedError):
            _record_network_failure("refused", str(exc.reason))
        else:
            _record_network_failure("urlerror", str(exc.reason))
        return False
    except (ssl.SSLError, ssl.CertificateError) as exc:
        _record_network_failure("ssl", str(exc))
        return False
    except (socket.timeout, TimeoutError) as exc:
        _record_network_failure("timeout", str(exc))
        return False
    except OSError as exc:
        _record_network_failure("oserror", str(exc))
        return False


# Thread-local or module-level hook for network-failure observability.
_network_failure_hook: Optional[Callable[[str, str], None]] = None


def set_network_failure_hook(hook: Optional[Callable[[str, str], None]]) -> None:
    """
    Register a callback ``(category: str, detail: str) -> None`` that is
    called whenever an internet-access probe fails.  Useful for telemetry.
    """
    global _network_failure_hook
    _network_failure_hook = hook


def _record_network_failure(category: str, detail: str) -> None:
    if _network_failure_hook:
        try:
            _network_failure_hook(category, detail)
        except Exception:
            pass  # never let a hook break the probe


async def has_internet_access() -> bool:
    """Async wrapper around the cached synchronous probe."""
    import asyncio

    return await asyncio.to_thread(_has_internet_access_cached)


def has_internet_access_sync() -> bool:
    """Synchronous entry-point (bypasses the async wrapper)."""
    return _has_internet_access_cached()


# ---------------------------------------------------------------------------
# Command / tool availability
# ---------------------------------------------------------------------------

def _is_command_available_sync(command: str) -> bool:
    """Check whether *command* is on PATH (sync)."""
    from hare.utils.which import which_sync

    return which_sync(command) is not None


async def _is_command_available_async(command: str) -> bool:
    """Check whether *command* is on PATH (async)."""
    from hare.utils.which import which

    try:
        return await which(command) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Package manager detection
# ---------------------------------------------------------------------------

_PACKAGE_MANAGER_NAMES = ("npm", "yarn", "pnpm")
_RUNTIME_NAMES = ("bun", "deno", "node")


@lru_cache(maxsize=1)
def _detect_package_managers_sync() -> Tuple[str, ...]:
    out: List[str] = []
    for name in _PACKAGE_MANAGER_NAMES:
        if _is_command_available_sync(name):
            out.append(name)
    return tuple(out)


@lru_cache(maxsize=1)
def _detect_runtimes_sync() -> Tuple[str, ...]:
    out: List[str] = []
    for name in _RUNTIME_NAMES:
        if _is_command_available_sync(name):
            out.append(name)
    return tuple(out)


async def detect_package_managers_async() -> Tuple[str, ...]:
    """Async variant that probes each manager individually."""
    out: List[str] = []
    for name in _PACKAGE_MANAGER_NAMES:
        if await _is_command_available_async(name):
            out.append(name)
    return tuple(out)


async def detect_runtimes_async() -> Tuple[str, ...]:
    """Async variant that probes each runtime individually."""
    out: List[str] = []
    for name in _RUNTIME_NAMES:
        if await _is_command_available_async(name):
            out.append(name)
    return tuple(out)


# ---------------------------------------------------------------------------
# WSL helpers
# ---------------------------------------------------------------------------

def _is_wsl_environment() -> bool:
    """Return True when running under Windows Subsystem for Linux."""
    try:
        return Path("/proc/sys/fs/binfmt_misc/WSLInterop").is_file()
    except OSError:
        return False


def _is_wsl_v1() -> bool:
    """
    Attempt to detect WSL v1 (vs v2).

    WSL v1 typically reports a different kernel version string.
    Returns True if WSL v1 is detected, False for v2 or non-WSL.
    """
    if not _is_wsl_environment():
        return False
    try:
        release = platform.release()
        # WSL v1 kernel strings contain "Microsoft" while WSL v2 uses a
        # standard Linux kernel.
        return "Microsoft" in release and "microsoft" not in release
    except Exception:
        return False


@lru_cache(maxsize=1)
def _is_npm_from_windows_path() -> bool:
    """Check if npm resolves to a Windows-hosted binary inside WSL."""
    if not _is_wsl_environment():
        return False
    try:
        cmd = find_executable("npm", [])["cmd"]
        return str(cmd).startswith("/mnt/c/")
    except (OSError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Conductor / IDE detection
# ---------------------------------------------------------------------------

def _is_conductor() -> bool:
    """True when the process is hosted by Conductor.app (macOS)."""
    return os.environ.get("__CFBundleIdentifier") == "com.conductor.app"


JETBRAINS_IDES = (
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
    "jetbrains",
    "androidstudio",
)


# ---------------------------------------------------------------------------
# SSH session detection
# ---------------------------------------------------------------------------

def _is_ssh_session() -> bool:
    """Return True when the current process appears to be in an SSH session."""
    return bool(
        os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_CLIENT")
        or os.environ.get("SSH_TTY")
    )


# ---------------------------------------------------------------------------
# Terminal detection
# ---------------------------------------------------------------------------

def detect_terminal() -> Optional[str]:
    """
    Identify the terminal emulator from environment variables.

    Returns the terminal name (lowercase), or ``None`` if it cannot be
    determined.  The detection order mirrors the TS implementation exactly.
    """
    # Cursor
    if os.environ.get("CURSOR_TRACE_ID"):
        return "cursor"
    ask = os.environ.get("VSCODE_GIT_ASKPASS_MAIN") or ""
    if "cursor" in ask:
        return "cursor"
    if "windsurf" in ask:
        return "windsurf"
    if "antigravity" in ask:
        return "antigravity"

    # macOS bundle identifiers
    bundle = (os.environ.get("__CFBundleIdentifier") or "").lower()
    if "vscodium" in bundle:
        return "codium"
    if "windsurf" in bundle:
        return "windsurf"
    if "com.google.android.studio" in bundle:
        return "androidstudio"
    if bundle:
        for ide in JETBRAINS_IDES:
            if ide in bundle:
                return ide

    # Windows Visual Studio
    if os.environ.get("VisualStudioVersion"):
        return "visualstudio"

    # JetBrains JedTerm
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        return "pycharm"  # generic fallback; envDynamic refines this

    # TERM-based detection (before TERM_PROGRAM to handle mismatches)
    if os.environ.get("TERM") == "xterm-ghostty":
        return "ghostty"
    term = os.environ.get("TERM") or ""
    if "kitty" in term:
        return "kitty"

    # TERM_PROGRAM
    if os.environ.get("TERM_PROGRAM"):
        return os.environ["TERM_PROGRAM"]

    # Multiplexers
    if os.environ.get("TMUX"):
        return "tmux"
    if os.environ.get("STY"):
        return "screen"

    # Linux terminals (environment-variable based)
    if os.environ.get("KONSOLE_VERSION"):
        return "konsole"
    if os.environ.get("GNOME_TERMINAL_SERVICE"):
        return "gnome-terminal"
    if os.environ.get("XTERM_VERSION"):
        return "xterm"
    if os.environ.get("VTE_VERSION"):
        return "vte-based"
    if os.environ.get("TERMINATOR_UUID"):
        return "terminator"
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    if os.environ.get("ALACRITTY_LOG"):
        return "alacritty"
    if os.environ.get("TILIX_ID"):
        return "tilix"

    # Windows-specific
    if os.environ.get("WT_SESSION"):
        return "windows-terminal"
    if os.environ.get("SESSIONNAME") and os.environ.get("TERM") == "cygwin":
        return "cygwin"
    if os.environ.get("MSYSTEM"):
        return os.environ["MSYSTEM"].lower()
    if (
        os.environ.get("ConEmuANSI")
        or os.environ.get("ConEmuPID")
        or os.environ.get("ConEmuTask")
    ):
        return "conemu"

    # WSL
    if os.environ.get("WSL_DISTRO_NAME"):
        return f"wsl-{os.environ['WSL_DISTRO_NAME']}"

    # SSH
    if _is_ssh_session():
        return "ssh-session"

    # TERM fallback (catch specific terminal names embedded in TERM)
    if os.environ.get("TERM"):
        t = os.environ["TERM"]
        if "alacritty" in t:
            return "alacritty"
        if "rxvt" in t:
            return "rxvt"
        if "termite" in t:
            return "termite"
        return t

    # Non-interactive
    if not sys.stdout.isatty():
        return "non-interactive"

    return None


# ---------------------------------------------------------------------------
# Shell detection
# ---------------------------------------------------------------------------

def get_shell() -> Optional[str]:
    """
    Detect the user's login shell.

    Checks ``SHELL`` env var first, then falls back to ``COMSPEC`` on
    Windows or ``/bin/sh`` path lookup.
    """
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC")
    if shell:
        name = Path(shell).name.lower()
        # Normalize known shell names
        return _normalize_shell_name(name)

    # Windows fallback via COMSPEC
    if _normalized_platform() == "win32":
        comspec = os.environ.get("COMSPEC")
        if comspec:
            return Path(comspec).name.lower()

    # POSIX: try to resolve via password database
    try:
        import pwd

        return _normalize_shell_name(Path(pwd.getpwuid(os.getuid()).pw_shell).name)
    except (ImportError, KeyError, OSError):
        pass

    return None


def _normalize_shell_name(name: str) -> str:
    """Normalize common shell binary names to canonical identifiers."""
    if name in ("bash", "sh"):
        return name
    if name.startswith("zsh"):
        return "zsh"
    if name.startswith("fish"):
        return "fish"
    if "powershell" in name or name == "pwsh":
        return "powershell"
    if name == "cmd" or name == "cmd.exe":
        return "cmd"
    if name in ("dash", "ash", "ksh", "tcsh", "csh"):
        return name
    if name.startswith("nu") or name == "nushell":
        return "nushell"
    return name


# ---------------------------------------------------------------------------
# Editor detection
# ---------------------------------------------------------------------------

def get_editor() -> Optional[str]:
    """
    Detect the user's preferred text editor.

    Checks ``VISUAL``, ``EDITOR``, then common editor paths.
    """
    for var in ("VISUAL", "EDITOR"):
        editor = os.environ.get(var)
        if editor:
            name_lower = Path(editor).name.lower()
            # Try to resolve symlinks for the binary name
            resolved = shutil.which(editor)
            if resolved:
                return _normalize_editor_name(Path(resolved).name.lower())
            return _normalize_editor_name(name_lower)

    # Auto-detect from well-known editors on PATH
    for candidate in ("code", "nvim", "vim", "nano", "emacs", "subl", "gedit", "notepad"):
        if shutil.which(candidate):
            return candidate

    return None


def _normalize_editor_name(name: str) -> str:
    """Normalize editor binary names to canonical identifiers."""
    if name in ("code", "code-insiders", "codium"):
        return "vscode"
    if name in ("nvim", "nvim-qt", "neovide"):
        return "neovim"
    if name in ("gvim", "mvim", "vimx"):
        return "vim"
    if name in ("emacs", "emacsclient", "emacs-nox"):
        return "emacs"
    if name in ("subl", "sublime_text", "subl3"):
        return "sublime"
    if name == "idea" or name == "idea.sh":
        return "intellij"
    return name


# ---------------------------------------------------------------------------
# Deployment / hosting environment detection
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def detect_deployment_environment() -> str:
    """
    Identify the hosting / deployment platform from well-known env vars.

    Returns a short platform identifier string or ``"unknown"`` /
    ``"unknown-<platform>"`` when nothing is detected.
    """
    # Cloud development environments
    if is_env_truthy(os.environ.get("CODESPACES")):
        return "codespaces"
    if os.environ.get("GITPOD_WORKSPACE_ID"):
        return "gitpod"
    if os.environ.get("REPL_ID") or os.environ.get("REPL_SLUG"):
        return "replit"
    if os.environ.get("PROJECT_DOMAIN"):
        return "glitch"

    # Cloud platforms
    if is_env_truthy(os.environ.get("VERCEL")):
        return "vercel"
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_SERVICE_NAME"):
        return "railway"
    if is_env_truthy(os.environ.get("RENDER")):
        return "render"
    if is_env_truthy(os.environ.get("NETLIFY")):
        return "netlify"
    if os.environ.get("DYNO"):
        return "heroku"
    if os.environ.get("FLY_APP_NAME") or os.environ.get("FLY_MACHINE_ID"):
        return "fly.io"
    if is_env_truthy(os.environ.get("CF_PAGES")):
        return "cloudflare-pages"
    if os.environ.get("DENO_DEPLOYMENT_ID"):
        return "deno-deploy"
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "aws-lambda"
    if os.environ.get("AWS_EXECUTION_ENV") == "AWS_ECS_FARGATE":
        return "aws-fargate"
    if os.environ.get("AWS_EXECUTION_ENV") == "AWS_ECS_EC2":
        return "aws-ecs"

    # EC2 detection via hypervisor UUID
    try:
        uuid_path = Path("/sys/hypervisor/uuid")
        if uuid_path.is_file():
            u = uuid_path.read_text(encoding="utf-8").strip().lower()
            if u.startswith("ec2"):
                return "aws-ec2"
    except OSError:
        pass

    # GCP
    if os.environ.get("K_SERVICE"):
        return "gcp-cloud-run"
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return "gcp"

    # Azure
    if os.environ.get("WEBSITE_SITE_NAME") or os.environ.get("WEBSITE_SKU"):
        return "azure-app-service"
    if os.environ.get("AZURE_FUNCTIONS_ENVIRONMENT"):
        return "azure-functions"

    # DigitalOcean
    app_url = os.environ.get("APP_URL") or ""
    if "ondigitalocean.app" in app_url:
        return "digitalocean-app-platform"

    # Hugging Face
    if os.environ.get("SPACE_CREATOR_USER_ID"):
        return "huggingface-spaces"

    # CI/CD platforms
    if is_env_truthy(os.environ.get("GITHUB_ACTIONS")):
        return "github-actions"
    if is_env_truthy(os.environ.get("GITLAB_CI")):
        return "gitlab-ci"
    if os.environ.get("CIRCLECI"):
        return "circleci"
    if os.environ.get("BUILDKITE"):
        return "buildkite"
    if os.environ.get("TRAVIS"):
        return "travis-ci"
    if os.environ.get("JENKINS_URL"):
        return "jenkins"
    if os.environ.get("TEAMCITY_VERSION"):
        return "teamcity"
    if os.environ.get("BITBUCKET_BUILD_NUMBER"):
        return "bitbucket-pipelines"
    if os.environ.get("DRONE"):
        return "drone"
    if is_env_truthy(os.environ.get("CI")):
        return "ci"

    # Container orchestration
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"

    # Generic container detection
    container = detect_container_environment()
    if container:
        return container

    # Platform fallbacks
    plat = _normalized_platform()
    if plat == "darwin":
        return "unknown-darwin"
    if plat == "linux":
        return "unknown-linux"
    if plat == "win32":
        return "unknown-win32"
    return "unknown"


# ---------------------------------------------------------------------------
# Container detection
# ---------------------------------------------------------------------------

def detect_container_environment() -> Optional[str]:
    """
    Detect container runtime (Docker, Podman, LXC, etc.).

    Returns a short identifier or ``None`` if no container is detected.
    Checks multiple signals:
    - ``/.dockerenv`` file presence
    - cgroup v1 / v2 container indicators
    - ``container`` environment variable (systemd / podman convention)
    - ``/run/.containerenv`` (podman)
    """
    # /.dockerenv (Docker)
    try:
        if Path("/.dockerenv").is_file():
            return "docker"
    except OSError:
        pass

    # /run/.containerenv (Podman)
    try:
        if Path("/run/.containerenv").is_file():
            return "podman"
    except OSError:
        pass

    # container env var (systemd-nspawn, podman, etc.)
    container_env = os.environ.get("container")
    if container_env:
        container_lower = container_env.lower()
        if container_lower == "podman":
            return "podman"
        if container_lower in ("docker", "oci", "containerd"):
            return container_lower
        return "container"  # generic

    # cgroup inspection
    try:
        cgroup_content = Path("/proc/1/cgroup").read_text(encoding="utf-8")
        if "docker" in cgroup_content or "libpod" in cgroup_content:
            if "libpod" in cgroup_content:
                return "podman"
            return "docker"
        if "/lxc/" in cgroup_content or "lxc" in cgroup_content:
            return "lxc"
        if "containerd" in cgroup_content:
            return "containerd"
        # Kubernetes containers have /kubepods/ in cgroup
        if "/kubepods/" in cgroup_content or "/kubelet/" in cgroup_content:
            return "kubernetes"
    except (OSError, PermissionError):
        pass

    # cgroup v2 container check
    try:
        cgroup_v2 = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        if "containerd" in cgroup_v2:
            return "containerd"
    except (OSError, PermissionError):
        pass

    return None


# ---------------------------------------------------------------------------
# Headless / display detection
# ---------------------------------------------------------------------------

def is_headless() -> bool:
    """
    Return True when no graphical display is available.

    Checks ``DISPLAY`` (X11), ``WAYLAND_DISPLAY`` (Wayland), and
    platform-specific signals (SSH, macOS Aqua session).
    """
    plat = _normalized_platform()

    if plat == "win32":
        # On Windows, check if there's a desktop session
        session_name = os.environ.get("SESSIONNAME")
        if session_name and "Console" in session_name:
            return False
        return not bool(os.environ.get("DISPLAY"))

    if plat == "darwin":
        # macOS: check for Aqua session
        if os.environ.get("TERM_PROGRAM"):
            return False
        return _is_ssh_session()

    # Linux / other
    has_display = bool(
        os.environ.get("DISPLAY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("MIR_SOCKET")
    )

    if has_display:
        return False

    # SSH sessions are often headless
    if _is_ssh_session():
        return True

    return True


# ---------------------------------------------------------------------------
# Terminal size
# ---------------------------------------------------------------------------

@dataclass
class TerminalSize:
    """Terminal dimensions in characters."""

    columns: int
    rows: int


def get_terminal_size() -> Optional[TerminalSize]:
    """
    Get the current terminal dimensions.

    Returns ``None`` when stdout is not a TTY or the size cannot be determined.
    """
    if not sys.stdout.isatty():
        return None

    try:
        size = shutil.get_terminal_size(fallback=(0, 0))
        if size.columns > 0 and size.lines > 0:
            return TerminalSize(columns=size.columns, rows=size.lines)
    except (OSError, ValueError):
        pass

    # Fallback: parse COLUMNS/LINES environment variables
    try:
        cols = int(os.environ.get("COLUMNS", "0"))
        rows = int(os.environ.get("LINES", "0"))
        if cols > 0 and rows > 0:
            return TerminalSize(columns=cols, rows=rows)
    except (ValueError, TypeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Virtual environment detection
# ---------------------------------------------------------------------------

def is_virtual_environment() -> bool:
    """
    Return True when running inside a Python virtual environment.

    Checks ``VIRTUAL_ENV``, ``CONDA_PREFIX``, ``PIPENV_ACTIVE``, ``POETRY_ACTIVE``,
    and ``sys.prefix != sys.base_prefix``.
    """
    if os.environ.get("VIRTUAL_ENV"):
        return True
    if os.environ.get("CONDA_PREFIX"):
        return True
    if os.environ.get("PIPENV_ACTIVE"):
        return True
    if os.environ.get("POETRY_ACTIVE"):
        return True
    try:
        return sys.prefix != sys.base_prefix
    except AttributeError:
        return hasattr(sys, "real_prefix")  # older virtualenv


def get_virtual_environment_type() -> Optional[str]:
    """
    Return the type of virtual environment, or None.

    Returns one of: ``"venv"``, ``"conda"``, ``"pipenv"``, ``"poetry"``,
    ``"virtualenv"``, ``"uv"``.
    """
    if os.environ.get("CONDA_PREFIX"):
        return "conda"
    if os.environ.get("PIPENV_ACTIVE"):
        return "pipenv"
    if os.environ.get("POETRY_ACTIVE"):
        return "poetry"
    if os.environ.get("UV"):
        return "uv"
    if os.environ.get("VIRTUAL_ENV"):
        return "virtualenv"
    try:
        if sys.prefix != sys.base_prefix:
            return "venv"
    except AttributeError:
        pass
    return None


# ---------------------------------------------------------------------------
# Python version info
# ---------------------------------------------------------------------------

@dataclass
class PythonVersionInfo:
    """Structured Python version information."""

    major: int
    minor: int
    micro: int
    releaselevel: str
    serial: int
    version_string: str  # e.g. "3.12.3"
    implementation: str  # e.g. "CPython", "PyPy"


def get_python_version_info() -> PythonVersionInfo:
    """Return structured information about the running Python interpreter."""
    vi = sys.version_info
    return PythonVersionInfo(
        major=vi.major,
        minor=vi.minor,
        micro=vi.micro,
        releaselevel=vi.releaselevel,
        serial=vi.serial,
        version_string=f"{vi.major}.{vi.minor}.{vi.micro}",
        implementation=platform.python_implementation(),
    )


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _normalized_platform() -> PlatformName:
    """Map ``sys.platform`` to our canonical PlatformName."""
    p = sys.platform
    if p in ("win32", "darwin"):
        return p  # type: ignore[return-value]
    return "linux"


def get_platform_string() -> str:
    """Return a human-readable platform name (Windows / macOS / Linux)."""
    return _PLATFORM_FRIENDLY.get(_normalized_platform(), "Unknown")


def get_platform_name() -> PlatformName:
    """Return the canonical platform identifier."""
    return _normalized_platform()


def is_macos() -> bool:
    """True when running on macOS."""
    return _normalized_platform() == "darwin"


def is_linux() -> bool:
    """True when running on Linux."""
    return _normalized_platform() == "linux"


def is_windows() -> bool:
    """True when running on Windows."""
    return _normalized_platform() == "win32"


# ---------------------------------------------------------------------------
# Bundled / bun mode
# ---------------------------------------------------------------------------

@runtime_checkable
class _BundledMode(Protocol):
    def is_running_with_bun(self) -> bool: ...


_bundled: Optional[_BundledMode] = None


def set_bundled_mode_provider(provider: Optional[_BundledMode]) -> None:
    """Inject a bundled-mode runtime provider (for testing / bundling)."""
    global _bundled
    _bundled = provider


def _is_running_with_bun() -> bool:
    if _bundled:
        return _bundled.is_running_with_bun()
    return False


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------

_host_arch: str = platform.machine() or ""


def get_arch() -> str:
    """Return the CPU architecture string (e.g. ``x86_64``, ``arm64``)."""
    return _host_arch


def get_arch_normalized() -> str:
    """
    Return a normalized architecture name suitable for analytics grouping.

    Maps common variants to canonical identifiers:
    - ``x86_64`` / ``amd64`` / ``AMD64`` → ``x86_64``
    - ``aarch64`` / ``arm64`` / ``ARM64`` → ``arm64``
    """
    a = _host_arch.lower()
    if a in ("x86_64", "amd64", "x64"):
        return "x86_64"
    if a in ("aarch64", "arm64", "armv8", "armv8l", "armv8b"):
        return "arm64"
    if a.startswith("armv7"):
        return "arm32"
    return a


# ---------------------------------------------------------------------------
# EnvNamespace  (mirrors the TS `env` object)
# ---------------------------------------------------------------------------

class EnvNamespace:
    """
    Namespace exposing all environment probes as attributes.

    Static attributes (platform, arch, …) are snapshotted at import time.
    Callable attributes (has_internet_access, detect_deployment_environment, …)
    are exposed as staticmethods so callers can use ``env.xxx()`` directly.
    """

    has_internet_access = staticmethod(has_internet_access)
    is_ci: bool = is_env_truthy(os.environ.get("CI"))
    platform: PlatformName = _normalized_platform()
    arch: str = _host_arch
    node_version: str = sys.version  # kept for API compat; use get_python_version_info() for structured data
    terminal: Optional[str] = detect_terminal()
    is_ssh = staticmethod(_is_ssh_session)

    get_package_managers = staticmethod(lambda: list(_detect_package_managers_sync()))
    get_runtimes = staticmethod(lambda: list(_detect_runtimes_sync()))

    get_package_managers_async = staticmethod(detect_package_managers_async)
    get_runtimes_async = staticmethod(detect_runtimes_async)

    is_running_with_bun = staticmethod(_is_running_with_bun)
    is_wsl_environment = staticmethod(_is_wsl_environment)
    is_npm_from_windows_path = staticmethod(_is_npm_from_windows_path)
    is_conductor = staticmethod(_is_conductor)

    detect_deployment_environment = staticmethod(detect_deployment_environment)

    # Additional convenience helpers
    get_shell = staticmethod(get_shell)
    get_editor = staticmethod(get_editor)
    is_headless = staticmethod(is_headless)
    is_virtual_environment = staticmethod(is_virtual_environment)
    get_virtual_environment_type = staticmethod(get_virtual_environment_type)
    get_terminal_size = staticmethod(get_terminal_size)
    detect_container_environment = staticmethod(detect_container_environment)
    get_arch_normalized = staticmethod(get_arch_normalized)
    get_platform_string = staticmethod(get_platform_string)
    get_python_version_info = staticmethod(get_python_version_info)


env = EnvNamespace()


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

def get_host_platform_for_analytics() -> PlatformName:
    """
    Return the platform identifier for analytics reporting.

    If ``CLAUDE_CODE_HOST_PLATFORM`` is set to a valid platform value it
    overrides the detected platform.  Useful for container/remote envs where
    ``sys.platform`` reports the container OS rather than the real host.
    """
    override = os.environ.get("CLAUDE_CODE_HOST_PLATFORM")
    if override in ("win32", "darwin", "linux"):
        return override  # type: ignore[return-value]
    return env.platform


# ---------------------------------------------------------------------------
# Environment summary (aggregate all probes)
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentSummary:
    """Aggregate snapshot of all detected environment characteristics."""

    platform: PlatformName
    platform_friendly: str
    arch: str
    arch_normalized: str
    python_version: PythonVersionInfo
    terminal: Optional[str]
    shell: Optional[str]
    editor: Optional[str]
    deployment: str
    container: Optional[str]
    is_ci: bool
    is_ssh: bool
    is_wsl: bool
    is_headless: bool
    is_virtual_env: bool
    virtual_env_type: Optional[str]
    terminal_size: Optional[TerminalSize]
    package_managers: List[str] = field(default_factory=list)
    runtimes: List[str] = field(default_factory=list)

    @classmethod
    def collect(cls) -> EnvironmentSummary:
        """Probe the current environment and return a populated summary."""
        return cls(
            platform=_normalized_platform(),
            platform_friendly=get_platform_string(),
            arch=_host_arch,
            arch_normalized=get_arch_normalized(),
            python_version=get_python_version_info(),
            terminal=detect_terminal(),
            shell=get_shell(),
            editor=get_editor(),
            deployment=detect_deployment_environment(),
            container=detect_container_environment(),
            is_ci=is_env_truthy(os.environ.get("CI")),
            is_ssh=_is_ssh_session(),
            is_wsl=_is_wsl_environment(),
            is_headless=is_headless(),
            is_virtual_env=is_virtual_environment(),
            virtual_env_type=get_virtual_environment_type(),
            terminal_size=get_terminal_size(),
            package_managers=list(_detect_package_managers_sync()),
            runtimes=list(_detect_runtimes_sync()),
        )


def get_environment_summary() -> EnvironmentSummary:
    """Convenience function — equivalent to ``EnvironmentSummary.collect()``."""
    return EnvironmentSummary.collect()
