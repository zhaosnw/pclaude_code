"""`hare doctor` installation and environment diagnostics (`doctorDiagnostic.ts`).

Provides comprehensive system diagnostics:
  - Installation type and path detection
  - Platform and runtime environment checks
  - Dependency availability (git, ripgrep, node, shell tools)
  - Configuration and settings status
  - Network / API connectivity checks
  - Project / worktree status
  - Warnings aggregation with severity levels
"""

from __future__ import annotations

import asyncio
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import importlib as _importlib

from hare.utils.diag_logs import log_for_diagnostics_no_pii
from hare.utils.platform import get_platform, Platform

__all__ = [
    "BinaryStatus",
    "ConfigStatus",
    "DiagnosticWarning",
    "DoctorDiagnostic",
    "GitStatus",
    "InstallationInfo",
    "NetworkStatus",
    "ProjectStatus",
    "RipgrepStatus",
    "SystemResourceStatus",
    "diagnostic_has_errors",
    "diagnostic_to_json",
    "format_diagnostic_json",
    "format_diagnostic_markdown",
    "format_diagnostic_text",
    "get_diagnostic_summary",
    "get_doctor_diagnostic",
    "get_doctor_diagnostic_cached",
    "get_doctor_diagnostic_robust",
    "get_doctor_diagnostic_sync",
    "get_system_health_score",
    "run_and_print_diagnostic",
]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

InstallationType = Literal[
    "npm-global",
    "npm-local",
    "native",
    "package-manager",
    "pip",
    "development",
    "unknown",
]

DiagnosticSeverity = Literal["ok", "warning", "error", "info"]


# ---------------------------------------------------------------------------
# Structured diagnostic result types
# ---------------------------------------------------------------------------

@dataclass
class InstallationInfo:
    type: InstallationType
    version: str
    path: str
    invoked_binary: str
    config_install_method: str
    multiple_installations: list[str] = field(default_factory=list)


@dataclass
class BinaryStatus:
    name: str
    available: bool
    path: str | None = None
    version: str | None = None
    notes: str | None = None


@dataclass
class RipgrepStatus:
    working: bool
    mode: Literal["system", "embedded", "missing"]
    system_path: str | None = None
    version: str | None = None


@dataclass
class GitStatus:
    available: bool
    version: str | None = None
    path: str | None = None
    is_repo: bool = False
    repo_root: str | None = None
    current_branch: str | None = None
    in_transient_state: bool = False


@dataclass
class ConfigStatus:
    config_dir: str
    config_dir_exists: bool
    settings_file_exists: bool
    settings_local_file_exists: bool
    credentials_file_exists: bool
    mcp_configs: list[str] = field(default_factory=list)
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class NetworkStatus:
    api_reachable: bool | None = None  # None = not checked
    proxy_configured: bool = False
    proxy_url: str | None = None
    ssl_verify: bool = True
    ca_bundle_path: str | None = None


@dataclass
class ProjectStatus:
    cwd: str
    is_git_repo: bool = False
    git_root: str | None = None
    claude_md_exists: bool = False
    claude_md_local_exists: bool = False
    settings_json_exists: bool = False
    gitignore_exists: bool = False
    in_worktree: bool = False
    worktree_list: list[str] = field(default_factory=list)


@dataclass
class SystemResourceStatus:
    """System resource information (disk, memory, CPU)."""

    disk_free_bytes: int = 0
    disk_total_bytes: int = 0
    disk_percent_free: float = 0.0
    disk_mount_point: str = ""
    memory_total_bytes: int = 0
    memory_available_bytes: int = 0
    cpu_count_logical: int = 0
    cpu_count_physical: int = 0
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0


@dataclass
class DiagnosticWarning:
    category: str
    severity: DiagnosticSeverity
    message: str
    suggestion: str | None = None


@dataclass
class DoctorDiagnostic:
    installation: InstallationInfo
    platform: Platform
    runtime: dict[str, str]
    binaries: list[BinaryStatus]
    ripgrep: RipgrepStatus
    git: GitStatus
    config: ConfigStatus
    network: NetworkStatus
    project: ProjectStatus
    system_resources: SystemResourceStatus = field(default_factory=SystemResourceStatus)
    warnings: list[DiagnosticWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installationType": self.installation.type,
            "version": self.installation.version,
            "installationPath": self.installation.path,
            "invokedBinary": self.installation.invoked_binary,
            "configInstallMethod": self.installation.config_install_method,
            "hasUpdatePermissions": _check_update_permissions(),
            "multipleInstallations": self.installation.multiple_installations,
            "platform": self.platform,
            "runtime": self.runtime,
            "binaries": [
                {
                    "name": b.name,
                    "available": b.available,
                    "path": b.path,
                    "version": b.version,
                    "notes": b.notes,
                }
                for b in self.binaries
            ],
            "ripgrepStatus": {
                "working": self.ripgrep.working,
                "mode": self.ripgrep.mode,
                "systemPath": self.ripgrep.system_path,
                "version": self.ripgrep.version,
            },
            "gitStatus": {
                "available": self.git.available,
                "version": self.git.version,
                "path": self.git.path,
                "isRepo": self.git.is_repo,
                "repoRoot": self.git.repo_root,
                "currentBranch": self.git.current_branch,
                "inTransientState": self.git.in_transient_state,
            },
            "configStatus": {
                "configDir": self.config.config_dir,
                "configDirExists": self.config.config_dir_exists,
                "settingsFileExists": self.config.settings_file_exists,
                "settingsLocalFileExists": self.config.settings_local_file_exists,
                "credentialsFileExists": self.config.credentials_file_exists,
                "mcpConfigs": self.config.mcp_configs,
                "envOverrides": self.config.env_overrides,
            },
            "networkStatus": {
                "apiReachable": self.network.api_reachable,
                "proxyConfigured": self.network.proxy_configured,
                "proxyUrl": self.network.proxy_url,
                "sslVerify": self.network.ssl_verify,
                "caBundlePath": self.network.ca_bundle_path,
            },
            "projectStatus": {
                "cwd": self.project.cwd,
                "isGitRepo": self.project.is_git_repo,
                "gitRoot": self.project.git_root,
                "claudeMdExists": self.project.claude_md_exists,
                "claudeMdLocalExists": self.project.claude_md_local_exists,
                "settingsJsonExists": self.project.settings_json_exists,
                "gitignoreExists": self.project.gitignore_exists,
                "inWorktree": self.project.in_worktree,
                "worktreeList": self.project.worktree_list,
            },
            "systemResources": {
                "diskFreeBytes": self.system_resources.disk_free_bytes,
                "diskTotalBytes": self.system_resources.disk_total_bytes,
                "diskPercentFree": self.system_resources.disk_percent_free,
                "diskMountPoint": self.system_resources.disk_mount_point,
                "memoryTotalBytes": self.system_resources.memory_total_bytes,
                "memoryAvailableBytes": self.system_resources.memory_available_bytes,
                "cpuCountLogical": self.system_resources.cpu_count_logical,
                "cpuCountPhysical": self.system_resources.cpu_count_physical,
                "swapTotalBytes": self.system_resources.swap_total_bytes,
                "swapUsedBytes": self.system_resources.swap_used_bytes,
            },
            "warnings": [
                {
                    "category": w.category,
                    "severity": w.severity,
                    "message": w.message,
                    "suggestion": w.suggestion,
                }
                for w in self.warnings
            ],
        }


# ---------------------------------------------------------------------------
# Installation detection
# ---------------------------------------------------------------------------

async def get_current_installation_type() -> InstallationType:
    """Detect how hare was installed."""
    # Check for development mode
    if os.environ.get("NODE_ENV") == "development":
        return "development"
    if os.environ.get("HARE_DEV") == "1":
        return "development"

    # Check for editable pip install
    try:
        import hare

        hare_file = getattr(hare, "__file__", "")
        if hare_file and ("site-packages" not in hare_file and "dist-packages" not in hare_file):
            # Installed in development / editable mode
            if ".venv" in hare_file or "venv" in hare_file:
                return "development"
    except ImportError:
        pass

    # Check standard pip installation
    try:
        import hare

        hare_file = getattr(hare, "__file__", "")
        if hare_file and ("site-packages" in hare_file or "dist-packages" in hare_file):
            return "pip"
    except ImportError:
        pass

    # Check for npm-style local installation
    local_hare_path = _get_local_hare_path()
    if local_hare_path and os.path.exists(local_hare_path):
        return "npm-local"

    # Check for native installation
    if _is_native_install():
        return "native"

    return "unknown"


def _get_local_hare_path() -> str | None:
    """Check for local npm-style installation under ~/.hare/local."""
    try:
        from hare.utils.env_utils import get_hare_config_home_dir
        local_dir = Path(get_hare_config_home_dir()) / "local"
        if local_dir.exists():
            return str(local_dir)
    except Exception:
        pass
    return None


def _is_native_install() -> bool:
    """Check if running as a native (compiled/bundled) installation."""
    # Native installs typically have the binary at a fixed system path
    if getattr(sys, "frozen", False):
        return True
    # Check for common native install markers
    exe_path = Path(sys.executable).resolve()
    if "hare" in exe_path.name and "node_modules" not in str(exe_path):
        native_paths = [
            "/usr/local/bin/hare",
            "/opt/homebrew/bin/hare",
            "/usr/bin/hare",
        ]
        if str(exe_path) in native_paths:
            return True
    return False


def _get_installation_version() -> str:
    """Get the current hare version."""
    try:
        from hare import VERSION
        return VERSION
    except ImportError:
        pass
    try:
        from hare import __version__
        return __version__
    except ImportError:
        pass
    return "unknown"


def _get_installation_path() -> str:
    """Get the installation path of the hare package."""
    try:
        import hare
        hare_file = getattr(hare, "__file__", "")
        if hare_file:
            return str(Path(hare_file).resolve().parent)
    except ImportError:
        pass
    return ""


def _get_invoked_binary() -> str:
    """Get the path of the binary that invoked this process."""
    return sys.executable


def _get_config_install_method() -> str:
    """Determine how configuration was set up."""
    config_dir = _get_config_dir()
    if not os.path.exists(config_dir):
        return "not set"
    try:
        from hare.utils.config import load_config
        cfg = load_config()
        if cfg and cfg.get("installMethod"):
            return cfg["installMethod"]
    except Exception:
        pass
    return "not set"


def _detect_multiple_installations() -> list[str]:
    """Detect multiple hare installations on the system."""
    installations: list[str] = []

    # Check pip installations
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "hare"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Location:"):
                    installations.append(line.split(":", 1)[1].strip())
    except Exception:
        pass

    # Check npm installations
    npm_global = Path.home() / ".npm" / "global" / "node_modules" / "@anthropic-ai" / "claude-code"
    if npm_global.exists():
        installations.append(str(npm_global))

    # Check local npm
    local = _get_local_hare_path()
    if local and local not in installations:
        installations.append(local)

    # Check homebrew
    brew_path = Path("/opt/homebrew/bin/hare")
    if brew_path.exists() or brew_path.is_symlink():
        installations.append(str(brew_path))

    # Check /usr/local/bin
    usr_local = Path("/usr/local/bin/hare")
    if usr_local.exists() and str(usr_local) not in installations:
        installations.append(str(usr_local))

    return installations


def _check_update_permissions() -> bool | None:
    """Check whether the current user has permission to update the installation."""
    install_path = _get_installation_path()
    if not install_path:
        return None
    try:
        test_file = Path(install_path) / ".write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except (OSError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# Platform and runtime checks
# ---------------------------------------------------------------------------

def _get_runtime_info() -> dict[str, str]:
    """Collect Python runtime and environment information."""
    return {
        "pythonVersion": sys.version,
        "pythonExecutable": sys.executable,
        "pythonPrefix": sys.prefix,
        "platformSystem": _platform.system(),
        "platformRelease": _platform.release(),
        "platformMachine": _platform.machine(),
        "platformVersion": _platform.version(),
        "shell": os.environ.get("SHELL", "unknown"),
        "terminal": os.environ.get("TERM", "unknown"),
        "terminalProgram": os.environ.get("TERM_PROGRAM", ""),
        "home": str(Path.home()),
        "tmpdir": os.environ.get("TMPDIR", os.environ.get("TEMP", "/tmp")),
        "lang": os.environ.get("LANG", "unknown"),
        "encoding": sys.getdefaultencoding(),
    }


# ---------------------------------------------------------------------------
# Binary / dependency checks
# ---------------------------------------------------------------------------

async def _check_binary(name: str, version_args: list[str] | None = None) -> BinaryStatus:
    """Check if a binary is available and optionally get its version."""
    path = shutil.which(name)
    version: str | None = None
    notes: str | None = None

    if path and version_args:
        # _run_command handles all exceptions internally (returns CompletedProcess
        # with returncode=-1 on failure), so these except blocks guard against
        # truly unexpected errors in argument construction / await itself.
        try:
            proc = await _run_command(
                [name] + version_args,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout:
                version = proc.stdout.strip().split("\n")[0]
            elif proc.returncode == -1:
                notes = "execution failed"
        except asyncio.TimeoutError:
            notes = "timeout"
        except Exception as exc:
            notes = f"version check failed: {exc}"

    return BinaryStatus(
        name=name,
        available=path is not None,
        path=path,
        version=version,
        notes=notes,
    )


async def _check_all_binaries() -> list[BinaryStatus]:
    """Check all relevant system binaries."""
    binaries_to_check = [
        ("git", ["--version"]),
        ("node", ["--version"]),
        ("npm", ["--version"]),
        ("npx", ["--version"]),
        ("gh", ["--version"]),
        ("docker", ["--version"]),
        ("ssh", ["-V"]),  # ssh -V writes to stderr
        ("curl", ["--version"]),
        ("wget", ["--version"]),
        ("make", ["--version"]),
        ("gcc", ["--version"]),
        ("g++", ["--version"]),
        ("python3", ["--version"]),
        ("pip", ["--version"]),
        ("fd", ["--version"]),
        ("bat", ["--version"]),
        ("fzf", ["--version"]),
        ("tmux", ["-V"]),
        ("nvim", ["--version"]),
        ("vim", ["--version"]),
    ]

    results: list[BinaryStatus] = []
    for name, version_args in binaries_to_check:
        result = await _check_binary(name, version_args)
        results.append(result)
    return results


async def _run_command(
    args: list[str],
    timeout: int = 10,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command safely and return its result."""
    try:
        proc = await _asyncio_subprocess_run(args, timeout=timeout, cwd=cwd)
        return proc
    except Exception:
        return subprocess.CompletedProcess(args, -1, stdout="", stderr="")


async def _asyncio_subprocess_run(
    args: list[str],
    timeout: int = 10,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Wrapper around asyncio.create_subprocess_exec for compatibility."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return subprocess.CompletedProcess(
        args,
        proc.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
        stderr=stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
    )


# ---------------------------------------------------------------------------
# Ripgrep diagnostics
# ---------------------------------------------------------------------------

async def _check_ripgrep() -> RipgrepStatus:
    """Check ripgrep availability and mode."""
    system_path = shutil.which("rg")
    version: str | None = None
    working = False
    mode: Literal["system", "embedded", "missing"] = "missing"

    if system_path:
        mode = "system"
        try:
            proc = await _run_command(["rg", "--version"], timeout=5)
            if proc.returncode == 0 and proc.stdout:
                version = proc.stdout.strip().split("\n")[0]
                working = True
        except Exception:
            pass
    else:
        # Check for embedded ripgrep
        embedded_paths = [
            Path(__file__).parent.parent / "vendor" / "ripgrep" / "rg",
            Path(sys.prefix) / "share" / "hare" / "vendor" / "ripgrep" / "rg",
        ]
        for ep in embedded_paths:
            if ep.exists():
                mode = "embedded"
                system_path = str(ep)
                try:
                    proc = await _run_command([str(ep), "--version"], timeout=5)
                    if proc.returncode == 0:
                        version = proc.stdout.strip().split("\n")[0]
                        working = True
                except Exception:
                    pass
                break

    return RipgrepStatus(
        working=working,
        mode=mode,
        system_path=system_path,
        version=version,
    )


# ---------------------------------------------------------------------------
# Git diagnostics
# ---------------------------------------------------------------------------

async def _check_git(cwd: str | None = None) -> GitStatus:
    """Comprehensive git availability and status check."""
    git_path = shutil.which("git")
    version: str | None = None
    is_repo = False
    repo_root: str | None = None
    branch: str | None = None
    in_transient = False

    if git_path:
        try:
            proc = await _run_command(["git", "--version"], timeout=5)
            if proc.returncode == 0:
                version = proc.stdout.strip()
        except Exception:
            pass

        # Check if in a git repo
        try:
            work_dir = cwd or os.getcwd()
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--show-toplevel",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                is_repo = True
                repo_root = stdout.decode("utf-8").strip()
        except Exception:
            pass

        # Get current branch
        if is_repo:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "rev-parse", "--abbrev-ref", "HEAD",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=repo_root,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode == 0:
                    branch = stdout.decode("utf-8").strip()
            except Exception:
                pass

            # Check transient state
            in_transient = _check_transient_git_state(repo_root)

    return GitStatus(
        available=git_path is not None,
        version=version,
        path=git_path,
        is_repo=is_repo,
        repo_root=repo_root,
        current_branch=branch,
        in_transient_state=in_transient,
    )


def _check_transient_git_state(git_root: str) -> bool:
    """Check for active rebase, merge, cherry-pick, bisect state."""
    git_dir = os.path.join(git_root, ".git")
    markers = [
        os.path.join(git_dir, "MERGE_HEAD"),
        os.path.join(git_dir, "rebase-merge"),
        os.path.join(git_dir, "rebase-apply"),
        os.path.join(git_dir, "CHERRY_PICK_HEAD"),
        os.path.join(git_dir, "BISECT_LOG"),
        os.path.join(git_dir, "REVERT_HEAD"),
    ]
    return any(os.path.exists(m) for m in markers)


# ---------------------------------------------------------------------------
# Configuration diagnostics
# ---------------------------------------------------------------------------

def _get_config_dir() -> str:
    """Get the hare config directory path."""
    try:
        from hare.utils.env_utils import get_hare_config_home_dir
        return get_hare_config_home_dir()
    except ImportError:
        return os.environ.get("HARE_CONFIG_DIR") or str(Path.home() / ".hare")


def _check_config(cwd: str) -> ConfigStatus:
    """Check configuration state."""
    config_dir = _get_config_dir()
    config_dir_exists = os.path.isdir(config_dir)

    settings_file = os.path.join(config_dir, "settings.json")
    settings_local_file = os.path.join(config_dir, "settings.local.json")
    credentials_file = os.path.join(config_dir, "credentials.json")

    # Detect MCP configs
    mcp_configs: list[str] = []
    mcp_dir = os.path.join(config_dir, "mcp")
    if os.path.isdir(mcp_dir):
        try:
            for f in os.listdir(mcp_dir):
                if f.endswith(".json"):
                    mcp_configs.append(f)
        except OSError:
            pass

    # Detect project-level .mcp.json
    project_mcp = os.path.join(cwd, ".mcp.json")
    if os.path.exists(project_mcp):
        mcp_configs.append(".mcp.json (project)")

    # Collect relevant env var overrides
    env_keys = [
        "CLAUDE_CODE_API_KEY",
        "CLAUDE_CODE_MODEL",
        "CLAUDE_CODE_MAX_TURNS",
        "CLAUDE_CODE_PERMISSION_MODE",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_DISABLE_TERMINAL_TITLE",
        "CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "HARE_CONFIG_DIR",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ]
    env_overrides: dict[str, str] = {}
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            # Redact sensitive values
            if "KEY" in key or "SECRET" in key:
                env_overrides[key] = "(set, value redacted)"
            else:
                env_overrides[key] = val

    return ConfigStatus(
        config_dir=config_dir,
        config_dir_exists=config_dir_exists,
        settings_file_exists=os.path.isfile(settings_file),
        settings_local_file_exists=os.path.isfile(settings_local_file),
        credentials_file_exists=os.path.isfile(credentials_file),
        mcp_configs=mcp_configs,
        env_overrides=env_overrides,
    )


# ---------------------------------------------------------------------------
# Network diagnostics
# ---------------------------------------------------------------------------

async def _check_network() -> NetworkStatus:
    """Check network configuration and API reachability."""
    proxy_configured = False
    proxy_url: str | None = None
    ssl_verify = True
    ca_bundle_path: str | None = None

    # Detect proxy settings
    for proxy_env in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(proxy_env)
        if val:
            proxy_configured = True
            proxy_url = val
            break

    # Check SSL settings
    if os.environ.get("REQUESTS_CA_BUNDLE"):
        ca_bundle_path = os.environ["REQUESTS_CA_BUNDLE"]
    elif os.environ.get("CURL_CA_BUNDLE"):
        ca_bundle_path = os.environ["CURL_CA_BUNDLE"]
    elif os.environ.get("SSL_CERT_FILE"):
        ca_bundle_path = os.environ["SSL_CERT_FILE"]

    # Check for SSL verify disable
    if os.environ.get("CLAUDE_CODE_SSL_VERIFY", "").lower() in ("false", "0", "no"):
        ssl_verify = False

    # Quick API reachability check (lightweight)
    api_reachable: bool | None = None
    try:
        writer: asyncio.StreamWriter | None = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("api.anthropic.com", 443),
                timeout=5,
            )
            api_reachable = True
        finally:
            if writer:
                writer.close()
    except Exception:
        api_reachable = False

    return NetworkStatus(
        api_reachable=api_reachable,
        proxy_configured=proxy_configured,
        proxy_url=proxy_url,
        ssl_verify=ssl_verify,
        ca_bundle_path=ca_bundle_path,
    )


# ---------------------------------------------------------------------------
# Project / worktree diagnostics
# ---------------------------------------------------------------------------

def _check_project(cwd: str, is_git_repo: bool, git_root: str | None) -> ProjectStatus:
    """Check project-level files and worktree status."""
    root = git_root or cwd

    claude_md = os.path.join(root, "CLAUDE.md")
    claude_md_local = os.path.join(root, "CLAUDE.local.md")
    settings_json = os.path.join(root, ".claude", "settings.json")
    settings_json_local = os.path.join(root, ".claude", "settings.local.json")
    gitignore = os.path.join(root, ".gitignore")

    in_worktree = False
    worktree_list: list[str] = []

    if is_git_repo:
        try:
            result = subprocess.run(
                ["git", "worktree", "list"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=root,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                worktree_list = [line.strip() for line in lines if line.strip()]
                # Check if CWD is a worktree (not the main repo)
                cwd_resolved = os.path.realpath(cwd)
                for wt_line in worktree_list:
                    wt_path = wt_line.split()[0]
                    if os.path.realpath(wt_path) == cwd_resolved:
                        if "bare" not in wt_line.lower() and not wt_line.endswith(" (bare)"):
                            in_worktree = "bare" not in wt_line.lower()
                        break
        except Exception:
            pass

    return ProjectStatus(
        cwd=cwd,
        is_git_repo=is_git_repo,
        git_root=git_root,
        claude_md_exists=os.path.isfile(claude_md),
        claude_md_local_exists=os.path.isfile(claude_md_local),
        settings_json_exists=os.path.isfile(settings_json) or os.path.isfile(settings_json_local),
        gitignore_exists=os.path.isfile(gitignore),
        in_worktree=in_worktree,
        worktree_list=worktree_list,
    )


# ---------------------------------------------------------------------------
# Warnings collection
# ---------------------------------------------------------------------------

def _collect_warnings(
    installation: InstallationInfo,
    ripgrep: RipgrepStatus,
    git: GitStatus,
    config: ConfigStatus,
    network: NetworkStatus,
    project: ProjectStatus,
    binaries: list[BinaryStatus],
) -> list[DiagnosticWarning]:
    """Collect all diagnostic warnings across categories."""
    warnings: list[DiagnosticWarning] = []

    # Installation warnings
    if installation.type == "unknown":
        warnings.append(DiagnosticWarning(
            category="installation",
            severity="warning",
            message="Could not determine installation type",
            suggestion="Reinstall hare via pip or check your PATH configuration.",
        ))
    if installation.multiple_installations:
        warnings.append(DiagnosticWarning(
            category="installation",
            severity="warning",
            message=f"Multiple hare installations detected: {', '.join(installation.multiple_installations)}",
            suggestion="Remove duplicate installations to avoid version conflicts.",
        ))

    # Ripgrep warnings
    if not ripgrep.working:
        warnings.append(DiagnosticWarning(
            category="ripgrep",
            severity="error",
            message="Ripgrep (rg) is not available — code search and file indexing will be degraded",
            suggestion="Install ripgrep: brew install ripgrep (macOS), apt install ripgrep (Linux), or choco install ripgrep (Windows).",
        ))
    elif ripgrep.mode == "missing":
        warnings.append(DiagnosticWarning(
            category="ripgrep",
            severity="warning",
            message="Ripgrep is not installed — features will use fallback search methods",
            suggestion="Install ripgrep for faster code search performance.",
        ))

    # Git warnings
    if not git.available:
        warnings.append(DiagnosticWarning(
            category="git",
            severity="error",
            message="Git is not available — version control features will not work",
            suggestion="Install git: https://git-scm.com/downloads",
        ))
    elif git.is_repo and git.in_transient_state:
        warnings.append(DiagnosticWarning(
            category="git",
            severity="warning",
            message="Repository is in a transient git state (rebase/merge/cherry-pick in progress)",
            suggestion="Complete or abort the ongoing git operation before making changes.",
        ))

    # Config warnings
    if not config.config_dir_exists:
        warnings.append(DiagnosticWarning(
            category="config",
            severity="info",
            message=f"Config directory does not exist: {config.config_dir}",
            suggestion="Run hare once to initialize configuration automatically.",
        ))
    if not config.credentials_file_exists and not os.environ.get("ANTHROPIC_API_KEY"):
        warnings.append(DiagnosticWarning(
            category="config",
            severity="error",
            message="No API credentials found — set ANTHROPIC_API_KEY or run 'hare login'",
            suggestion="Run 'hare login' or set the ANTHROPIC_API_KEY environment variable.",
        ))

    # Network warnings
    if network.api_reachable is False:
        warnings.append(DiagnosticWarning(
            category="network",
            severity="error",
            message="Cannot reach api.anthropic.com — check your network connection and firewall",
            suggestion="Verify internet connectivity, check proxy settings, or ensure VPN is connected.",
        ))
    if network.proxy_configured:
        warnings.append(DiagnosticWarning(
            category="network",
            severity="info",
            message=f"HTTP proxy configured: {network.proxy_url}",
            suggestion=None,
        ))
    if not network.ssl_verify:
        warnings.append(DiagnosticWarning(
            category="network",
            severity="warning",
            message="SSL certificate verification is disabled",
            suggestion="Enable SSL verification for security. Unset CLAUDE_CODE_SSL_VERIFY or set it to 'true'.",
        ))

    # Project warnings
    if not project.claude_md_exists and not project.claude_md_local_exists:
        warnings.append(DiagnosticWarning(
            category="project",
            severity="info",
            message="No CLAUDE.md or CLAUDE.local.md found in project root",
            suggestion="Create a CLAUDE.md file to provide project-specific instructions to hare.",
        ))
    if project.in_worktree:
        warnings.append(DiagnosticWarning(
            category="project",
            severity="info",
            message="Running inside a git worktree — session isolation applies",
            suggestion=None,
        ))

    # Binary warnings
    essential_missing = [
        b for b in binaries
        if not b.available and b.name in ("git", "node", "curl")
    ]
    for b in essential_missing:
        warnings.append(DiagnosticWarning(
            category="binaries",
            severity="warning",
            message=f"Essential binary '{b.name}' is not available",
            suggestion=f"Install {b.name} for full functionality.",
        ))

    return warnings


# ---------------------------------------------------------------------------
# Main diagnostic entry point
# ---------------------------------------------------------------------------

async def get_doctor_diagnostic(cwd: str | None = None) -> dict[str, Any]:
    """Run all diagnostic checks and return a comprehensive report.

    This is the main entry point called by the `/doctor` command and any
    programmatic diagnostic consumers. It performs:

    1. Installation detection (type, path, version, duplicates)
    2. Platform and runtime environment checks
    3. Binary dependency checks (git, node, ripgrep, etc.)
    4. Git repository status
    5. Configuration status (settings, credentials, MCP)
    6. Network connectivity
    7. Project file presence (CLAUDE.md, .gitignore, worktrees)
    8. Warning aggregation across all categories

    Returns a dict with all diagnostic data suitable for display or JSON export.
    """
    work_dir = cwd or os.getcwd()

    log_for_diagnostics_no_pii("info", "doctor_diagnostic_started")

    # 1. Installation
    installation = InstallationInfo(
        type=await get_current_installation_type(),
        version=_get_installation_version(),
        path=_get_installation_path(),
        invoked_binary=_get_invoked_binary(),
        config_install_method=_get_config_install_method(),
        multiple_installations=_detect_multiple_installations(),
    )

    # 2. Platform
    platform = get_platform()

    # 3. Runtime
    runtime = _get_runtime_info()

    # 4. Binaries
    binaries = await _check_all_binaries()

    # 5. Ripgrep
    ripgrep = await _check_ripgrep()

    # 6. Git
    git = await _check_git(work_dir)

    # 7. Config
    config = _check_config(work_dir)

    # 8. Network
    network = await _check_network()

    # 9. Project
    project = _check_project(work_dir, git.is_repo, git.repo_root)

    # 10. System resources
    system_resources = _check_system_resources()

    # 11. Warnings
    warnings = _collect_warnings(installation, ripgrep, git, config, network, project, binaries)

    diagnostic = DoctorDiagnostic(
        installation=installation,
        platform=platform,
        runtime=runtime,
        binaries=binaries,
        ripgrep=ripgrep,
        git=git,
        config=config,
        network=network,
        project=project,
        system_resources=system_resources,
        warnings=warnings,
    )

    log_for_diagnostics_no_pii(
        "info",
        "doctor_diagnostic_completed",
        {
            "installation_type": installation.type,
            "platform": platform,
            "warning_count": len(warnings),
        },
    )

    return diagnostic.to_dict()


# ---------------------------------------------------------------------------
# Lightweight sync helper (for quick checks without running full diagnostics)
# ---------------------------------------------------------------------------

def get_doctor_diagnostic_sync() -> dict[str, Any]:
    """Synchronous, lightweight diagnostic snapshot (no network, no subprocess).

    Use this for fast in-REPL status checks where full async diagnostics
    would be too slow or disruptive. Includes a quick config check and
    the most important binary detections.
    """
    try:
        install_type = _detect_install_type_sync()
    except Exception:
        install_type = "unknown"

    installation = InstallationInfo(
        type=install_type,
        version=_get_installation_version(),
        path=_get_installation_path(),
        invoked_binary=_get_invoked_binary(),
        config_install_method=_get_config_install_method(),
        multiple_installations=[],  # skip subprocess-heavy detection
    )

    platform = get_platform()
    runtime = _get_runtime_info()
    rg_path = shutil.which("rg")
    git_path = shutil.which("git")
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    config_dir = _get_config_dir()
    config_dir_exists = os.path.isdir(config_dir)
    cwd = os.getcwd()

    # Quick project sniff (no subprocess calls)
    claude_md_exists = os.path.isfile(os.path.join(cwd, "CLAUDE.md"))
    claude_md_local_exists = os.path.isfile(os.path.join(cwd, "CLAUDE.local.md"))
    settings_exists = os.path.isfile(os.path.join(cwd, ".claude", "settings.json"))
    gitignore_exists = os.path.isfile(os.path.join(cwd, ".gitignore"))

    # Collect quick warnings without subprocess
    quick_warnings: list[dict[str, Any]] = []
    if not config_dir_exists:
        quick_warnings.append({
            "category": "config",
            "severity": "info",
            "message": f"Config directory does not exist: {config_dir}",
            "suggestion": "Run hare once to initialize configuration.",
        })
    if not git_path:
        quick_warnings.append({
            "category": "git",
            "severity": "warning",
            "message": "Git is not available",
            "suggestion": "Install git for version control features.",
        })
    if not rg_path:
        quick_warnings.append({
            "category": "ripgrep",
            "severity": "warning",
            "message": "Ripgrep (rg) is not available",
            "suggestion": "Install ripgrep for fast code search.",
        })

    return {
        "installationType": installation.type,
        "version": installation.version,
        "installationPath": installation.path,
        "invokedBinary": installation.invoked_binary,
        "platform": platform,
        "pythonVersion": runtime["pythonVersion"],
        "pythonExecutable": runtime["pythonExecutable"],
        "shell": runtime["shell"],
        "home": runtime["home"],
        "gitAvailable": git_path is not None,
        "gitPath": git_path,
        "rgAvailable": rg_path is not None,
        "rgPath": rg_path,
        "nodeAvailable": node_path is not None,
        "npmAvailable": npm_path is not None,
        "configDir": config_dir,
        "configDirExists": config_dir_exists,
        "cwd": cwd,
        "claudeMdExists": claude_md_exists,
        "claudeMdLocalExists": claude_md_local_exists,
        "projectSettingsExists": settings_exists,
        "gitignoreExists": gitignore_exists,
        "hasApiKey": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "quickWarnings": quick_warnings,
    }


def _detect_install_type_sync() -> InstallationType:
    """Synchronous, lightweight installation type detection (no subprocess)."""
    if os.environ.get("NODE_ENV") == "development" or os.environ.get("HARE_DEV") == "1":
        return "development"
    try:
        import hare
        hare_file = getattr(hare, "__file__", "")
        if hare_file:
            if "site-packages" in hare_file or "dist-packages" in hare_file:
                return "pip"
            if ".venv" in hare_file or "venv" in hare_file:
                return "development"
    except ImportError:
        pass
    if getattr(sys, "frozen", False):
        return "native"
    return "unknown"


# ---------------------------------------------------------------------------
# System resource diagnostics (disk, memory, CPU)
# ---------------------------------------------------------------------------

def _check_system_resources() -> SystemResourceStatus:
    """Collect system resource information: disk, memory, CPU.

    Gracefully degrades on platforms where resource information is unavailable.
    """
    result = SystemResourceStatus()

    # --- Disk ---
    try:
        cwd = os.getcwd()
        usage = shutil.disk_usage(cwd)
        result.disk_free_bytes = usage.free
        result.disk_total_bytes = usage.total
        if usage.total > 0:
            result.disk_percent_free = round(usage.free / usage.total * 100, 1)
        result.disk_mount_point = cwd
    except (OSError, PermissionError, FileNotFoundError):
        # Try home directory as fallback
        try:
            home = str(Path.home())
            usage = shutil.disk_usage(home)
            result.disk_free_bytes = usage.free
            result.disk_total_bytes = usage.total
            if usage.total > 0:
                result.disk_percent_free = round(usage.free / usage.total * 100, 1)
            result.disk_mount_point = home
        except (OSError, PermissionError):
            pass

    # --- CPU ---
    try:
        result.cpu_count_logical = os.cpu_count() or 0
    except Exception:
        pass

    try:
        # Physical cores: available on Linux via /proc/cpuinfo
        if _platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    siblings = set()
                    core_ids = set()
                    for line in f:
                        if line.startswith("physical id"):
                            siblings.add(line.strip())
                        elif line.startswith("core id"):
                            core_ids.add(line.strip())
                    result.cpu_count_physical = max(len(siblings or {1}), len(core_ids or {1}))
            except (OSError, PermissionError):
                result.cpu_count_physical = result.cpu_count_logical
        elif _platform.system() == "Darwin":
            try:
                proc = subprocess.run(
                    ["sysctl", "-n", "hw.physicalcpu"],
                    capture_output=True, text=True, timeout=5,
                )
                if proc.returncode == 0:
                    result.cpu_count_physical = int(proc.stdout.strip())
            except Exception:
                result.cpu_count_physical = result.cpu_count_logical
        else:
            result.cpu_count_physical = result.cpu_count_logical
    except Exception:
        pass

    # --- Memory / swap ---
    _collect_memory_info(result)

    return result


def _collect_memory_info(result: SystemResourceStatus) -> None:
    """Fill in memory and swap fields on result, best-effort per platform."""
    system = _platform.system()

    if system == "Linux":
        _collect_memory_linux(result)
    elif system == "Darwin":
        _collect_memory_macos(result)
    elif system == "Windows":
        _collect_memory_windows(result)


def _collect_memory_linux(result: SystemResourceStatus) -> None:
    """Parse /proc/meminfo for Linux memory stats."""
    try:
        meminfo: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) >= 2:
                    key = parts[0].strip()
                    val_str = parts[1].strip().split()[0]
                    try:
                        meminfo[key] = int(val_str) * 1024  # kB → bytes
                    except ValueError:
                        pass
        result.memory_total_bytes = meminfo.get("MemTotal", 0)
        result.memory_available_bytes = meminfo.get("MemAvailable", 0) or (
            meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
        )
        result.swap_total_bytes = meminfo.get("SwapTotal", 0)
        result.swap_used_bytes = meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)
    except (OSError, PermissionError):
        pass


def _collect_memory_macos(result: SystemResourceStatus) -> None:
    """Use sysctl for macOS memory stats."""
    try:
        # Physical memory
        proc = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            result.memory_total_bytes = int(proc.stdout.strip())
    except Exception:
        pass

    # Available memory approximation via vm_stat
    try:
        proc = subprocess.run(
            ["vm_stat"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            page_size = 16384  # default, will try to parse
            stats: dict[str, int] = {}
            for line in proc.stdout.splitlines():
                if "page size" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        try:
                            page_size = int(parts[1].strip().split()[0])
                        except ValueError:
                            pass
                elif ":" in line:
                    parts = line.split(":")
                    key = parts[0].strip().strip('"')
                    val_str = parts[1].strip().rstrip(".")
                    try:
                        stats[key] = int(val_str)
                    except ValueError:
                        pass
            free_pages = stats.get("Pages free", 0)
            speculative_pages = stats.get("Pages speculative", 0)
            inactive_pages = stats.get("Pages inactive", 0)
            available_pages = free_pages + speculative_pages + inactive_pages
            result.memory_available_bytes = available_pages * page_size
    except Exception:
        pass

    # Swap
    try:
        proc = subprocess.run(
            ["sysctl", "vm.swapusage"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            # Format: "vm.swapusage: total = 2048.00M  used = 512.00M  free = 1536.00M"
            import re
            total_m = re.search(r"total\s*=\s*([\d.]+)M", proc.stdout)
            used_m = re.search(r"used\s*=\s*([\d.]+)M", proc.stdout)
            if total_m:
                result.swap_total_bytes = int(float(total_m.group(1)) * 1024 * 1024)
            if used_m:
                result.swap_used_bytes = int(float(used_m.group(1)) * 1024 * 1024)
    except Exception:
        pass


def _collect_memory_windows(result: SystemResourceStatus) -> None:
    """Use wmic for Windows memory stats (best-effort)."""
    try:
        proc = subprocess.run(
            ["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/Value"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if "=" in line:
                    key, _, val = line.partition("=")
                    try:
                        bytes_val = int(val.strip()) * 1024  # kB → bytes
                        if "TotalVisibleMemorySize" in key:
                            result.memory_total_bytes = bytes_val
                        elif "FreePhysicalMemory" in key:
                            result.memory_available_bytes = bytes_val
                    except ValueError:
                        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Python package diagnostics
# ---------------------------------------------------------------------------

def _check_python_packages() -> dict[str, dict[str, str | None]]:
    """Check availability and versions of key Python packages.

    Returns a dict mapping package name to {version, path, importable}.
    Gracefully handles missing packages without raising exceptions.
    """
    packages_to_check = [
        "anthropic",
        "openai",
        "httpx",
        "aiohttp",
        "requests",
        "numpy",
        "pandas",
        "pydantic",
        "jsonschema",
        "cryptography",
        "jwt",
        "yaml",
        "toml",
        "rich",
        "click",
        "typer",
        "fastapi",
        "uvicorn",
        "starlette",
        "websockets",
        "pytest",
        "mypy",
        "ruff",
        "black",
        "gitpython",
        "tree_sitter",
    ]

    results: dict[str, dict[str, str | None]] = {}
    for pkg_name in packages_to_check:
        results[pkg_name] = _check_single_package(pkg_name)
    return results


def _check_single_package(pkg_name: str) -> dict[str, str | None]:
    """Check a single Python package availability and version."""
    try:
        mod = _importlib.import_module(pkg_name)
        version = getattr(mod, "__version__", None)
        path = getattr(mod, "__file__", None)
        return {
            "version": version,
            "path": str(path) if path else None,
            "importable": "true",
        }
    except ImportError:
        return {"version": None, "path": None, "importable": "false"}
    except Exception as exc:
        return {"version": None, "path": None, "importable": f"error: {exc}"}


# ---------------------------------------------------------------------------
# Skills / MCP diagnostics
# ---------------------------------------------------------------------------

def _check_installed_skills(config_dir: str) -> list[dict[str, str]]:
    """Detect installed skills from the config directory.

    Scans standard skill locations and returns a list of skill metadata.
    """
    skills: list[dict[str, str]] = []
    skill_dirs = [
        os.path.join(config_dir, "skills"),
        os.path.join(config_dir, "plugins"),
        os.path.join(os.getcwd(), ".claude", "skills"),
    ]

    for skills_root in skill_dirs:
        if not os.path.isdir(skills_root):
            continue
        try:
            for entry in os.listdir(skills_root):
                entry_path = os.path.join(skills_root, entry)
                if os.path.isdir(entry_path):
                    # Look for a manifest or main file
                    manifest = os.path.join(entry_path, "skill.json")
                    skill_md = os.path.join(entry_path, "SKILL.md")
                    if os.path.isfile(manifest) or os.path.isfile(skill_md):
                        skills.append({
                            "name": entry,
                            "path": entry_path,
                            "source": skills_root,
                        })
        except (OSError, PermissionError):
            pass

    return skills


def _check_mcp_servers(config_dir: str) -> dict[str, Any]:
    """Check configured MCP servers and their status.

    Returns aggregated MCP server information including configuration counts
    and any obvious misconfigurations.
    """
    mcp_info: dict[str, Any] = {
        "total_configs": 0,
        "servers": [],
        "project_mcp_exists": False,
        "global_mcp_dir_exists": False,
    }

    # Check global MCP directory
    global_mcp_dir = os.path.join(config_dir, "mcp")
    if os.path.isdir(global_mcp_dir):
        mcp_info["global_mcp_dir_exists"] = True
        try:
            for f in sorted(os.listdir(global_mcp_dir)):
                if f.endswith(".json"):
                    config_path = os.path.join(global_mcp_dir, f)
                    server_data = _parse_mcp_config_file(config_path)
                    if server_data:
                        mcp_info["servers"].append(server_data)
        except (OSError, PermissionError):
            pass

    # Check project-level .mcp.json
    project_mcp = os.path.join(os.getcwd(), ".mcp.json")
    if os.path.isfile(project_mcp):
        mcp_info["project_mcp_exists"] = True
        server_data = _parse_mcp_config_file(project_mcp)
        if server_data:
            server_data["source"] = "project"
            mcp_info["servers"].append(server_data)

    mcp_info["total_configs"] = len(mcp_info["servers"])
    return mcp_info


def _parse_mcp_config_file(config_path: str) -> dict[str, Any] | None:
    """Parse a single MCP configuration file for server info."""
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"file": os.path.basename(config_path), "error": "unreadable or invalid JSON"}

    servers_found: list[str] = []
    # Standard structure: { "mcpServers": { "name": {...}, ... } }
    if isinstance(data, dict):
        mcp_servers = data.get("mcpServers", data.get("mcp_servers", {}))
        if isinstance(mcp_servers, dict):
            servers_found = list(mcp_servers.keys())

    return {
        "file": os.path.basename(config_path),
        "path": config_path,
        "server_count": len(servers_found),
        "server_names": servers_found,
    }


# ---------------------------------------------------------------------------
# Update availability check
# ---------------------------------------------------------------------------

def _check_update_available() -> dict[str, Any]:
    """Check if a newer version of hare is available (best-effort, no network call).

    This is a lightweight check that only looks at locally available information.
    For a full check, the caller should perform a network-based version comparison.
    """
    current = _get_installation_version()
    return {
        "current_version": current,
        "checked": False,
        "update_available": None,
        "note": "Network-based version check not performed during diagnostics.",
    }


# ---------------------------------------------------------------------------
# Extended warnings collection (system resources, packages)
# ---------------------------------------------------------------------------

def _collect_extended_warnings(
    system_resources: SystemResourceStatus,
    python_packages: dict[str, dict[str, str | None]],
) -> list[DiagnosticWarning]:
    """Collect warnings related to system resources and Python packages."""
    warnings: list[DiagnosticWarning] = []

    # Disk space warnings
    if system_resources.disk_total_bytes > 0:
        if system_resources.disk_percent_free < 5:
            warnings.append(DiagnosticWarning(
                category="system",
                severity="error",
                message=f"Critically low disk space: {system_resources.disk_percent_free}% free "
                        f"({_format_bytes(system_resources.disk_free_bytes)} available)",
                suggestion="Free up disk space to avoid runtime errors and data loss.",
            ))
        elif system_resources.disk_percent_free < 10:
            warnings.append(DiagnosticWarning(
                category="system",
                severity="warning",
                message=f"Low disk space: {system_resources.disk_percent_free}% free "
                        f"({_format_bytes(system_resources.disk_free_bytes)} available)",
                suggestion="Consider freeing up disk space soon.",
            ))

    # Memory warnings
    if system_resources.memory_total_bytes > 0 and system_resources.memory_available_bytes > 0:
        avail_gb = system_resources.memory_available_bytes / (1024 ** 3)
        total_gb = system_resources.memory_total_bytes / (1024 ** 3)
        if total_gb < 4:
            warnings.append(DiagnosticWarning(
                category="system",
                severity="warning",
                message=f"Low total memory: {total_gb:.1f} GB — performance may be degraded",
                suggestion="At least 8 GB RAM is recommended for optimal performance.",
            ))
        elif avail_gb < 0.5:
            warnings.append(DiagnosticWarning(
                category="system",
                severity="warning",
                message=f"Low available memory: {avail_gb:.1f} GB free",
                suggestion="Close other applications to free memory.",
            ))

    # CPU warnings
    if 0 < system_resources.cpu_count_logical < 2:
        warnings.append(DiagnosticWarning(
            category="system",
            severity="info",
            message="Single CPU core detected — parallel operations may be limited",
            suggestion=None,
        ))

    # Package warnings
    essential_packages = {"anthropic": "Anthropic API client"}
    for pkg, label in essential_packages.items():
        pkg_info = python_packages.get(pkg, {})
        if pkg_info.get("importable") != "true":
            warnings.append(DiagnosticWarning(
                category="packages",
                severity="warning",
                message=f"Essential Python package '{pkg}' ({label}) is not available",
                suggestion=f"Install with: pip install {pkg}",
            ))

    return warnings


def _format_bytes(num_bytes: int) -> str:
    """Format byte count into a human-readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 ** 3:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{num_bytes / (1024 ** 3):.2f} GB"


# ---------------------------------------------------------------------------
# Robust main diagnostic entry point (per-section error resilience)
# ---------------------------------------------------------------------------

async def get_doctor_diagnostic_robust(
    cwd: str | None = None,
    *,
    include_network: bool = True,
    include_packages: bool = False,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run full diagnostics with per-section error isolation.

    Unlike `get_doctor_diagnostic`, this function wraps each diagnostic section
    in its own try/except so that a failure in one area (e.g. network timeout)
    does not prevent other sections from producing results.

    Args:
        cwd: Working directory to use. Defaults to os.getcwd().
        include_network: Whether to perform network reachability checks.
        include_packages: Whether to scan installed Python packages.
        timeout_seconds: Maximum total time for the entire diagnostic run.

    Returns a dict with the standard diagnostic schema plus an `_errors` key
    listing any sections that failed.
    """
    work_dir = cwd or os.getcwd()
    errors: list[dict[str, str]] = []
    start_time = _time.perf_counter()

    log_for_diagnostics_no_pii("info", "doctor_diagnostic_robust_started")

    # Helper to run a section with error isolation
    async def _safe_section(name: str, coro: Awaitable[Any], default: Any) -> Any:
        nonlocal errors
        elapsed = _time.perf_counter() - start_time
        if elapsed > timeout_seconds:
            errors.append({"section": name, "error": "timeout (skipped)"})
            return default
        try:
            remaining = timeout_seconds - elapsed
            if remaining < 1:
                errors.append({"section": name, "error": "insufficient time remaining"})
                return default
            return await asyncio.wait_for(coro, timeout=min(remaining, 10))
        except asyncio.TimeoutError:
            errors.append({"section": name, "error": "timeout"})
            return default
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})
            log_for_diagnostics_no_pii("warn", f"diagnostic_section_failed",
                                        {"section": name, "error": str(exc)})
            return default

    def _safe_section_sync(name: str, fn: Callable[[], Any], default: Any) -> Any:
        nonlocal errors
        try:
            return fn()
        except Exception as exc:
            errors.append({"section": name, "error": str(exc)})
            log_for_diagnostics_no_pii("warn", f"diagnostic_section_failed",
                                        {"section": name, "error": str(exc)})
            return default

    # 1. Installation
    install_type = await _safe_section(
        "installation_type", get_current_installation_type(), "unknown",
    )
    installation = _safe_section_sync("installation_info", lambda: InstallationInfo(
        type=install_type,
        version=_get_installation_version(),
        path=_get_installation_path(),
        invoked_binary=_get_invoked_binary(),
        config_install_method=_get_config_install_method(),
        multiple_installations=_detect_multiple_installations(),
    ), InstallationInfo(type="unknown", version="", path="", invoked_binary="",
                         config_install_method="not set"))

    # 2. Platform
    platform = _safe_section_sync("platform", get_platform, "linux")

    # 3. Runtime
    runtime = _safe_section_sync("runtime", _get_runtime_info, {})

    # 4. Binaries
    binaries = await _safe_section("binaries", _check_all_binaries(), [])

    # 5. Ripgrep
    ripgrep = await _safe_section(
        "ripgrep", _check_ripgrep(),
        RipgrepStatus(working=False, mode="missing"),
    )

    # 6. Git
    git = await _safe_section(
        "git", _check_git(work_dir),
        GitStatus(available=False),
    )

    # 7. Config
    config = _safe_section_sync(
        "config", lambda: _check_config(work_dir),
        ConfigStatus(
            config_dir="", config_dir_exists=False,
            settings_file_exists=False, settings_local_file_exists=False,
            credentials_file_exists=False,
        ),
    )

    # 8. Network (optional)
    network: NetworkStatus
    if include_network:
        network = await _safe_section("network", _check_network(), NetworkStatus())
    else:
        network = NetworkStatus()

    # 9. Project
    project = _safe_section_sync(
        "project", lambda: _check_project(work_dir, git.is_repo, git.repo_root),
        ProjectStatus(cwd=work_dir),
    )

    # 10. System resources
    system_resources = _safe_section_sync(
        "system_resources", _check_system_resources, SystemResourceStatus(),
    )

    # 11. Python packages (optional, slow)
    python_packages: dict[str, dict[str, str | None]] = {}
    if include_packages:
        python_packages = _safe_section_sync(
            "python_packages", _check_python_packages, {},
        )

    # 12. Skills and MCP
    skills = _safe_section_sync(
        "skills", lambda: _check_installed_skills(_get_config_dir()), [],
    )
    mcp_info = _safe_section_sync(
        "mcp", lambda: _check_mcp_servers(_get_config_dir()),
        {"total_configs": 0, "servers": [], "project_mcp_exists": False,
         "global_mcp_dir_exists": False},
    )

    # 13. Update check
    update_info = _safe_section_sync("update", _check_update_available, {})

    # 14. Warnings
    base_warnings = _safe_section_sync(
        "warnings_base",
        lambda: _collect_warnings(installation, ripgrep, git, config, network, project, binaries),
        [],
    )
    extended_warnings = _safe_section_sync(
        "warnings_extended",
        lambda: _collect_extended_warnings(system_resources, python_packages),
        [],
    )
    all_warnings = base_warnings + extended_warnings

    # Build diagnostic
    diagnostic = DoctorDiagnostic(
        installation=installation,
        platform=platform,
        runtime=runtime,
        binaries=binaries,
        ripgrep=ripgrep,
        git=git,
        config=config,
        network=network,
        project=project,
        system_resources=system_resources,
        warnings=all_warnings,
    )

    result = diagnostic.to_dict()
    # Inject extra sections that aren't in the standard to_dict
    result["skills"] = skills
    result["mcpStatus"] = mcp_info
    result["pythonPackages"] = python_packages
    result["updateInfo"] = update_info
    result["_diagnosticDurationMs"] = int((_time.perf_counter() - start_time) * 1000)
    if errors:
        result["_errors"] = errors

    log_for_diagnostics_no_pii(
        "info",
        "doctor_diagnostic_robust_completed",
        {
            "installation_type": installation.type,
            "platform": platform,
            "warning_count": len(all_warnings),
            "error_sections": len(errors),
            "duration_ms": result["_diagnosticDurationMs"],
        },
    )

    return result


# ---------------------------------------------------------------------------
# Cached diagnostic (memory cache, TTL-based)
# ---------------------------------------------------------------------------

_diagnostic_cache: dict[str, Any] | None = None
_diagnostic_cache_time: float = 0.0
_DIAGNOSTIC_CACHE_TTL: float = 60.0  # seconds


def _invalidate_diagnostic_cache() -> None:
    """Clear the in-memory diagnostic cache."""
    global _diagnostic_cache, _diagnostic_cache_time
    _diagnostic_cache = None
    _diagnostic_cache_time = 0.0


async def get_doctor_diagnostic_cached(
    cwd: str | None = None,
    *,
    force_refresh: bool = False,
    cache_ttl: float = _DIAGNOSTIC_CACHE_TTL,
) -> dict[str, Any]:
    """Get diagnostics, using a short-lived in-memory cache.

    Useful for rapid repeated calls (e.g. status bar updates) where running
    full diagnostics every time would be wasteful.

    Args:
        cwd: Working directory.
        force_refresh: If True, skip the cache and run fresh diagnostics.
        cache_ttl: Cache time-to-live in seconds. Default 60s.

    Returns diagnostic dict (same schema as get_doctor_diagnostic_robust).
    """
    global _diagnostic_cache, _diagnostic_cache_time
    now = _time.monotonic()

    if (not force_refresh
            and _diagnostic_cache is not None
            and (now - _diagnostic_cache_time) < cache_ttl):
        return _diagnostic_cache

    result = await get_doctor_diagnostic_robust(cwd)
    _diagnostic_cache = result
    _diagnostic_cache_time = now
    return result


# ---------------------------------------------------------------------------
# Formatting: markdown, plain text, JSON
# ---------------------------------------------------------------------------

def format_diagnostic_markdown(diagnostic: dict[str, Any]) -> str:
    """Format a diagnostic result dict as a readable Markdown report.

    Suitable for display in the terminal or embedding in issues.
    """
    lines: list[str] = []
    lines.append("# Hare Doctor Diagnostic Report")
    lines.append("")

    # --- Header ---
    lines.append("## Installation")
    lines.append(f"- **Type**: `{diagnostic.get('installationType', 'unknown')}`")
    lines.append(f"- **Version**: `{diagnostic.get('version', 'unknown')}`")
    lines.append(f"- **Path**: `{diagnostic.get('installationPath', 'N/A')}`")
    lines.append(f"- **Binary**: `{diagnostic.get('invokedBinary', 'N/A')}`")
    lines.append(f"- **Update permissions**: {_yes_no(diagnostic.get('hasUpdatePermissions'))}")
    multiples = diagnostic.get("multipleInstallations", [])
    if multiples:
        lines.append("- **Multiple installations detected:**")
        for m in multiples:
            lines.append(f"  - `{m}`")
    lines.append("")

    # --- Platform ---
    lines.append("## Platform & Runtime")
    lines.append(f"- **Platform**: `{diagnostic.get('platform', 'unknown')}`")
    rt = diagnostic.get("runtime", {})
    lines.append(f"- **Python**: `{rt.get('pythonVersion', 'unknown').split()[0]}`")
    lines.append(f"- **Shell**: `{rt.get('shell', 'unknown')}`")
    lines.append(f"- **Terminal**: `{rt.get('terminal', 'unknown')}`")
    lines.append(f"- **Encoding**: `{rt.get('encoding', 'unknown')}`")
    lines.append("")

    # --- Binaries ---
    lines.append("## System Binaries")
    lines.append("| Binary | Available | Version | Path |")
    lines.append("|--------|-----------|---------|------|")
    for b in diagnostic.get("binaries", []):
        avail = ":white_check_mark:" if b.get("available") else ":x:"
        ver = b.get("version", "—") or "—"
        path = f"`{b.get('path', '—')}`" if b.get("path") else "—"
        lines.append(f"| `{b.get('name', '?')}` | {avail} | {ver[:50]} | {path} |")
    lines.append("")

    # --- Ripgrep ---
    rg = diagnostic.get("ripgrepStatus", {})
    lines.append("## Ripgrep")
    lines.append(f"- **Mode**: `{rg.get('mode', 'unknown')}`")
    lines.append(f"- **Working**: {_yes_no(rg.get('working'))}")
    lines.append(f"- **Version**: `{rg.get('version', 'N/A')}`")
    lines.append(f"- **Path**: `{rg.get('systemPath', 'N/A')}`")
    lines.append("")

    # --- Git ---
    gs = diagnostic.get("gitStatus", {})
    lines.append("## Git")
    lines.append(f"- **Available**: {_yes_no(gs.get('available'))}")
    lines.append(f"- **Version**: `{gs.get('version', 'N/A')}`")
    lines.append(f"- **In repo**: {_yes_no(gs.get('isRepo'))}")
    if gs.get("repoRoot"):
        lines.append(f"- **Repo root**: `{gs.get('repoRoot')}`")
    if gs.get("currentBranch"):
        lines.append(f"- **Branch**: `{gs.get('currentBranch')}`")
    lines.append(f"- **Transient state**: {_yes_no(gs.get('inTransientState'))}")
    lines.append("")

    # --- Config ---
    cs = diagnostic.get("configStatus", {})
    lines.append("## Configuration")
    lines.append(f"- **Config dir**: `{cs.get('configDir', 'N/A')}`")
    lines.append(f"- **Config dir exists**: {_yes_no(cs.get('configDirExists'))}")
    lines.append(f"- **settings.json**: {_yes_no(cs.get('settingsFileExists'))}")
    lines.append(f"- **settings.local.json**: {_yes_no(cs.get('settingsLocalFileExists'))}")
    lines.append(f"- **Credentials file**: {_yes_no(cs.get('credentialsFileExists'))}")
    mcp_configs = cs.get("mcpConfigs", [])
    if mcp_configs:
        lines.append("- **MCP configs**:")
        for mc in mcp_configs:
            lines.append(f"  - `{mc}`")
    env_overrides = cs.get("envOverrides", {})
    if env_overrides:
        lines.append("- **Environment overrides**:")
        for k, v in env_overrides.items():
            lines.append(f"  - `{k}` = `{v}`")
    lines.append("")

    # --- Network ---
    ns = diagnostic.get("networkStatus", {})
    lines.append("## Network")
    api_reachable = ns.get("apiReachable")
    if api_reachable is True:
        lines.append("- **API reachable**: :white_check_mark: Yes")
    elif api_reachable is False:
        lines.append("- **API reachable**: :x: No")
    else:
        lines.append("- **API reachable**: :grey_question: Not checked")
    lines.append(f"- **Proxy**: {'Yes' if ns.get('proxyConfigured') else 'No'}")
    if ns.get("proxyUrl"):
        lines.append(f"- **Proxy URL**: `{ns.get('proxyUrl')}`")
    lines.append(f"- **SSL verify**: {_yes_no(ns.get('sslVerify'))}")
    lines.append("")

    # --- Project ---
    ps = diagnostic.get("projectStatus", {})
    lines.append("## Project")
    lines.append(f"- **CWD**: `{ps.get('cwd', 'N/A')}`")
    lines.append(f"- **Is git repo**: {_yes_no(ps.get('isGitRepo'))}")
    lines.append(f"- **CLAUDE.md**: {_yes_no(ps.get('claudeMdExists'))}")
    lines.append(f"- **CLAUDE.local.md**: {_yes_no(ps.get('claudeMdLocalExists'))}")
    lines.append(f"- **Project settings.json**: {_yes_no(ps.get('settingsJsonExists'))}")
    lines.append(f"- **.gitignore**: {_yes_no(ps.get('gitignoreExists'))}")
    lines.append(f"- **In worktree**: {_yes_no(ps.get('inWorktree'))}")
    worktrees = ps.get("worktreeList", [])
    if worktrees:
        lines.append("- **Worktrees**:")
        for wt in worktrees:
            lines.append(f"  - `{wt}`")
    lines.append("")

    # --- System Resources ---
    sr = diagnostic.get("systemResources", {})
    if sr:
        lines.append("## System Resources")
        if sr.get("diskTotalBytes"):
            lines.append(f"- **Disk**: {_format_bytes(sr.get('diskFreeBytes', 0))} free "
                         f"of {_format_bytes(sr.get('diskTotalBytes', 0))} "
                         f"({sr.get('diskPercentFree', 0)}%)")
        if sr.get("memoryTotalBytes"):
            lines.append(f"- **Memory**: {_format_bytes(sr.get('memoryAvailableBytes', 0))} available "
                         f"of {_format_bytes(sr.get('memoryTotalBytes', 0))}")
        if sr.get("cpuCountLogical"):
            lines.append(f"- **CPU**: {sr.get('cpuCountLogical')} logical, "
                         f"{sr.get('cpuCountPhysical')} physical cores")
        if sr.get("swapTotalBytes"):
            lines.append(f"- **Swap**: {_format_bytes(sr.get('swapUsedBytes', 0))} used "
                         f"of {_format_bytes(sr.get('swapTotalBytes', 0))}")
        lines.append("")

    # --- Warnings ---
    warnings_list = diagnostic.get("warnings", [])
    if warnings_list:
        severity_icon = {"ok": ":green_circle:", "warning": ":yellow_circle:",
                         "error": ":red_circle:", "info": ":blue_circle:"}
        lines.append("## Warnings")
        lines.append("| Severity | Category | Message |")
        lines.append("|----------|----------|---------|")
        for w in warnings_list:
            icon = severity_icon.get(w.get("severity", "info"), ":question:")
            lines.append(f"| {icon} | `{w.get('category', '?')}` | {w.get('message', '')} |")
        lines.append("")

        # Suggestions
        suggestions = [w for w in warnings_list if w.get("suggestion")]
        if suggestions:
            lines.append("### Suggestions")
            for i, w in enumerate(suggestions, 1):
                lines.append(f"{i}. [{w.get('category')}] {w.get('suggestion')}")
            lines.append("")
    else:
        lines.append("## Warnings")
        lines.append(":white_check_mark: No warnings detected.")
        lines.append("")

    # --- Errors from robust run ---
    run_errors = diagnostic.get("_errors", [])
    if run_errors:
        lines.append("## Diagnostic Errors")
        lines.append("Some diagnostic sections failed to run:")
        for e in run_errors:
            lines.append(f"- **{e.get('section')}**: {e.get('error')}")
        lines.append("")

    # Footer
    duration = diagnostic.get("_diagnosticDurationMs")
    if duration:
        lines.append(f"---\n*Diagnostic completed in {duration}ms*")
    else:
        lines.append("---\n*Generated by hare doctor*")

    return "\n".join(lines)


def format_diagnostic_text(diagnostic: dict[str, Any]) -> str:
    """Format a diagnostic result dict as a plain-text report.

    Strips markdown formatting for environments where markdown is not rendered.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("HARE DOCTOR DIAGNOSTIC REPORT")
    lines.append("=" * 60)

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        ("INSTALLATION", [
            ("Type", str(diagnostic.get("installationType", "unknown"))),
            ("Version", str(diagnostic.get("version", "unknown"))),
            ("Path", str(diagnostic.get("installationPath", "N/A"))),
            ("Binary", str(diagnostic.get("invokedBinary", "N/A"))),
            ("Update permissions", str(diagnostic.get("hasUpdatePermissions"))),
        ]),
        ("PLATFORM & RUNTIME", [
            ("Platform", str(diagnostic.get("platform", "unknown"))),
            ("Python", str(diagnostic.get("runtime", {}).get("pythonVersion", "unknown")).split()[0]),
            ("Shell", str(diagnostic.get("runtime", {}).get("shell", "unknown"))),
            ("Encoding", str(diagnostic.get("runtime", {}).get("encoding", "unknown"))),
        ]),
        ("RIPGREP", [
            ("Mode", str(diagnostic.get("ripgrepStatus", {}).get("mode", "unknown"))),
            ("Working", _yes_no(diagnostic.get("ripgrepStatus", {}).get("working"))),
        ]),
        ("GIT", [
            ("Available", _yes_no(diagnostic.get("gitStatus", {}).get("available"))),
            ("In repo", _yes_no(diagnostic.get("gitStatus", {}).get("isRepo"))),
            ("Branch", str(diagnostic.get("gitStatus", {}).get("currentBranch") or "N/A")),
        ]),
        ("NETWORK", [
            ("API reachable", _reachable_str(diagnostic.get("networkStatus", {}).get("apiReachable"))),
            ("Proxy", "Yes" if diagnostic.get("networkStatus", {}).get("proxyConfigured") else "No"),
            ("SSL verify", _yes_no(diagnostic.get("networkStatus", {}).get("sslVerify"))),
        ]),
        ("PROJECT", [
            ("CWD", str(diagnostic.get("projectStatus", {}).get("cwd", "N/A"))),
            ("Is git repo", _yes_no(diagnostic.get("projectStatus", {}).get("isGitRepo"))),
            ("CLAUDE.md", _yes_no(diagnostic.get("projectStatus", {}).get("claudeMdExists"))),
            ("In worktree", _yes_no(diagnostic.get("projectStatus", {}).get("inWorktree"))),
        ]),
    ]

    for section_title, fields in sections:
        lines.append("")
        lines.append(f"[{section_title}]")
        for label, value in fields:
            lines.append(f"  {label}: {value}")

    # System resources
    sr = diagnostic.get("systemResources", {})
    if sr.get("diskTotalBytes"):
        lines.append("")
        lines.append("[SYSTEM RESOURCES]")
        lines.append(f"  Disk free: {_format_bytes(sr.get('diskFreeBytes', 0))} "
                     f"/ {_format_bytes(sr.get('diskTotalBytes', 0))} "
                     f"({sr.get('diskPercentFree', 0)}%)")
        lines.append(f"  Memory available: {_format_bytes(sr.get('memoryAvailableBytes', 0))} "
                     f"/ {_format_bytes(sr.get('memoryTotalBytes', 0))}")
        lines.append(f"  CPU cores: {sr.get('cpuCountLogical')} logical, "
                     f"{sr.get('cpuCountPhysical')} physical")

    # Warnings
    warnings_list = diagnostic.get("warnings", [])
    lines.append("")
    lines.append("[WARNINGS]")
    if warnings_list:
        for w in warnings_list:
            prefix = {"error": "ERROR", "warning": "WARN", "info": "INFO", "ok": "OK"}.get(
                w.get("severity"), "?"
            )
            lines.append(f"  [{prefix}] [{w.get('category', '?')}] {w.get('message', '')}")
            if w.get("suggestion"):
                lines.append(f"    -> {w.get('suggestion')}")
    else:
        lines.append("  No warnings detected.")

    lines.append("")
    lines.append("=" * 60)
    duration = diagnostic.get("_diagnosticDurationMs")
    if duration:
        lines.append(f"Diagnostic completed in {duration}ms")
    return "\n".join(lines)


def format_diagnostic_json(diagnostic: dict[str, Any]) -> str:
    """Serialize a diagnostic result dict to a JSON string.

    Use this for structured output modes (e.g., `--output json` or piping).
    """
    return json.dumps(diagnostic, indent=2, ensure_ascii=False, default=str)


def diagnostic_to_json(
    diagnostic: dict[str, Any],
    *,
    exclude_sections: list[str] | None = None,
) -> str:
    """Serialize diagnostic to JSON, optionally excluding sections.

    Args:
        diagnostic: The diagnostic dict to serialize.
        exclude_sections: Top-level keys to omit from the output.

    Returns pretty-printed JSON string.
    """
    to_exclude = set(exclude_sections or [])
    filtered = {k: v for k, v in diagnostic.items() if k not in to_exclude}
    return json.dumps(filtered, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Summary and health score
# ---------------------------------------------------------------------------

def get_diagnostic_summary(diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Extract a concise summary from a diagnostic result.

    Returns a dict with just the key findings: errors, warnings, and a
    quick health assessment suitable for display in compact views.
    """
    warnings_list: list[dict[str, Any]] = diagnostic.get("warnings", [])

    errors = [w for w in warnings_list if w.get("severity") == "error"]
    warns = [w for w in warnings_list if w.get("severity") == "warning"]
    infos = [w for w in warnings_list if w.get("severity") == "info"]

    return {
        "version": diagnostic.get("version", "unknown"),
        "installation_type": diagnostic.get("installationType", "unknown"),
        "platform": diagnostic.get("platform", "unknown"),
        "error_count": len(errors),
        "warning_count": len(warns),
        "info_count": len(infos),
        "total_warnings": len(warnings_list),
        "git_available": diagnostic.get("gitStatus", {}).get("available", False),
        "rg_working": diagnostic.get("ripgrepStatus", {}).get("working", False),
        "api_reachable": diagnostic.get("networkStatus", {}).get("apiReachable"),
        "config_dir_exists": diagnostic.get("configStatus", {}).get("configDirExists", False),
        "credentials_exist": (
            diagnostic.get("configStatus", {}).get("credentialsFileExists", False)
            or bool(os.environ.get("ANTHROPIC_API_KEY"))
        ),
        "health_score": _compute_health_score(errors, warns),
        "top_issues": [
            {"severity": w.get("severity"), "category": w.get("category"),
             "message": w.get("message")}
            for w in (errors + warns)[:5]
        ],
    }


def _compute_health_score(
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> int:
    """Compute a simple health score from 0 (critical) to 100 (optimal).

    Scoring:
        Start at 100.
        - Each error: -25
        - Each warning: -10
        Clamped to [0, 100].
    """
    score = 100 - (len(errors) * 25) - (len(warnings) * 10)
    return max(0, min(100, score))


def diagnostic_has_errors(diagnostic: dict[str, Any]) -> bool:
    """Check if a diagnostic result contains any critical errors.

    Returns True if there is at least one warning with severity 'error'.
    """
    for w in diagnostic.get("warnings", []):
        if w.get("severity") == "error":
            return True
    return False


def get_system_health_score(diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Compute a detailed health score from a diagnostic result.

    Returns a dict with the numeric score, a text label, and a breakdown
    of contributing factors.
    """
    warnings_list: list[dict[str, Any]] = diagnostic.get("warnings", [])
    errors = [w for w in warnings_list if w.get("severity") == "error"]
    warns = [w for w in warnings_list if w.get("severity") == "warning"]

    score = _compute_health_score(errors, warns)

    if score >= 90:
        label = "excellent"
        icon = ":green_circle:"
    elif score >= 70:
        label = "good"
        icon = ":large_green_circle:"
    elif score >= 50:
        label = "fair"
        icon = ":yellow_circle:"
    elif score >= 25:
        label = "poor"
        icon = ":orange_circle:"
    else:
        label = "critical"
        icon = ":red_circle:"

    return {
        "score": score,
        "label": label,
        "icon": icon,
        "error_count": len(errors),
        "warning_count": len(warns),
        "total_warnings": len(warnings_list),
        "contributing_factors": [
            {"severity": w.get("severity"), "category": w.get("category"),
             "message": w.get("message")}
            for w in (errors + warns)
        ],
    }


# ---------------------------------------------------------------------------
# Small utility helpers
# ---------------------------------------------------------------------------

def _yes_no(value: Any) -> str:
    """Format a boolean-ish value as 'Yes' or 'No'."""
    if value is None:
        return "N/A"
    if value:
        return "Yes"
    return "No"


def _reachable_str(value: bool | None) -> str:
    """Format API reachability as a readable string."""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Not checked"


# ---------------------------------------------------------------------------
# Permission detail diagnostics
# ---------------------------------------------------------------------------

def _check_permissions_detail(install_path: str) -> dict[str, Any]:
    """Check detailed file permissions for the installation directory.

    Returns a dict with read/write/execute information.
    """
    result: dict[str, Any] = {
        "install_path": install_path,
        "install_path_exists": False,
        "can_read": False,
        "can_write": False,
        "can_execute": False,
        "can_update": False,
        "owner": None,
    }

    if not install_path:
        return result

    path = Path(install_path)
    result["install_path_exists"] = path.exists()

    if not path.exists():
        return result

    try:
        stat = path.stat()
        result["can_read"] = os.access(path, os.R_OK)
        result["can_write"] = os.access(path, os.W_OK)
        result["can_execute"] = os.access(path, os.X_OK)
        result["can_update"] = result["can_read"] and result["can_write"]
    except (OSError, PermissionError):
        pass

    # Determine owner
    try:
        import pwd
        pw = pwd.getpwuid(path.stat().st_uid)
        result["owner"] = pw.pw_name
    except (ImportError, KeyError, OSError):
        pass

    return result


# ---------------------------------------------------------------------------
# Convenience: run full diagnostic and print
# ---------------------------------------------------------------------------

async def run_and_print_diagnostic(
    cwd: str | None = None,
    *,
    output_format: Literal["markdown", "text", "json"] = "markdown",
    include_packages: bool = False,
) -> str:
    """Run full diagnostics and return a formatted string ready for display.

    This is the top-level convenience function for the `/doctor` command.
    """
    diagnostic = await get_doctor_diagnostic_robust(
        cwd, include_packages=include_packages,
    )

    if output_format == "json":
        return format_diagnostic_json(diagnostic)
    elif output_format == "text":
        return format_diagnostic_text(diagnostic)
    else:
        return format_diagnostic_markdown(diagnostic)
