"""
Sandbox adapter.

Port of: src/utils/sandbox/sandbox-adapter.ts

Bridge between external sandbox runtime and CLI settings/tool integration.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SandboxConfig:
    enabled: bool = False
    filesystem_allow_write: list[str] = field(default_factory=list)
    filesystem_deny_write: list[str] = field(default_factory=list)
    network_allow: list[str] = field(default_factory=list)
    network_deny: list[str] = field(default_factory=list)


def resolve_path_pattern_for_sandbox(
    pattern: str,
    source: str = "user",
    settings_root: str = "",
) -> str:
    """Resolve Hare-specific path patterns for sandbox-runtime."""
    if pattern.startswith("//"):
        return pattern[1:]
    if pattern.startswith("/") and not pattern.startswith("//"):
        root = settings_root or os.getcwd()
        return os.path.join(root, pattern[1:])
    return pattern


def resolve_sandbox_filesystem_path(
    pattern: str,
    source: str = "user",
    settings_root: str = "",
) -> str:
    """Resolve paths from sandbox.filesystem.* settings."""
    if pattern.startswith("//"):
        return pattern[1:]
    return os.path.expanduser(pattern) if pattern.startswith("~") else pattern


class SandboxManager:
    """Manages sandbox execution environment.

    Static methods for global sandbox state inspection (matching TS SandboxManager).
    """

    _global_sandbox_enabled: bool = False
    _global_auto_allow_bash_if_sandboxed: bool = False
    # None → defer to settings (TS reads it live). A non-None value is an explicit
    # programmatic override (set by /sandbox or tests).
    _global_unsandboxed_commands_allowed: bool | None = None

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self._config = config or SandboxConfig()
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._config.enabled:
            self._active = True

    def stop(self) -> None:
        self._active = False

    def is_path_allowed_write(self, path: str) -> bool:
        if not self._active:
            return True
        abs_path = os.path.abspath(path)
        for deny in self._config.filesystem_deny_write:
            if abs_path.startswith(deny):
                return False
        if self._config.filesystem_allow_write:
            return any(
                abs_path.startswith(a) for a in self._config.filesystem_allow_write
            )
        return True

    def is_network_allowed(self, host: str) -> bool:
        if not self._active:
            return True
        for deny in self._config.network_deny:
            if host == deny or host.endswith("." + deny):
                return False
        if self._config.network_allow:
            return any(
                host == a or host.endswith("." + a) for a in self._config.network_allow
            )
        return True

    @staticmethod
    def is_sandboxing_enabled() -> bool:
        """Check if sandboxing is enabled (TS: SandboxManager.isSandboxingEnabled).

        TS gates on platform support + dependency checks + settings.sandbox.enabled.
        hare honors an explicit programmatic override (the global flag, set by /sandbox
        or tests) OR settings.sandbox.enabled on a platform we can actually enforce on
        (macOS seatbelt). Without this, the global flag was only ever set by tests, so
        the feature was unreachable for real users."""
        if SandboxManager._global_sandbox_enabled:
            return True
        return _settings_sandbox_enabled() and _sandbox_platform_supported()

    @staticmethod
    def set_sandboxing_enabled(enabled: bool) -> None:
        """Set global sandboxing state."""
        SandboxManager._global_sandbox_enabled = enabled

    @staticmethod
    def is_auto_allow_bash_if_sandboxed_enabled() -> bool:
        """Check if auto-allow Bash when sandboxed is enabled (TS: SandboxManager.isAutoAllowBashIfSandboxedEnabled)."""
        return SandboxManager._global_auto_allow_bash_if_sandboxed

    @staticmethod
    def set_auto_allow_bash_if_sandboxed_enabled(enabled: bool) -> None:
        """Set global auto-allow Bash when sandboxed state."""
        SandboxManager._global_auto_allow_bash_if_sandboxed = enabled

    @staticmethod
    def are_unsandboxed_commands_allowed() -> bool:
        """Whether policy permits running commands outside the sandbox when the
        model passes dangerouslyDisableSandbox (TS:
        SandboxManager.areUnsandboxedCommandsAllowed → settings
        .sandbox.allowUnsandboxedCommands ?? true).

        An explicit programmatic override wins; otherwise the setting is read
        LIVE (default True) so a user who hardens the sandbox with
        allowUnsandboxedCommands=false can actually disable the escape hatch."""
        if SandboxManager._global_unsandboxed_commands_allowed is not None:
            return SandboxManager._global_unsandboxed_commands_allowed
        return _settings_sandbox_bool("allowUnsandboxedCommands", True)

    @staticmethod
    def set_unsandboxed_commands_allowed(allowed: bool) -> None:
        SandboxManager._global_unsandboxed_commands_allowed = allowed


def get_sandbox_config() -> SandboxConfig:
    """Get sandbox configuration from settings.

    NOTE: this is a minimal stub — it does NOT reproduce TS
    convertToSandboxRuntimeConfig, which also derives allow/deny-write roots
    (temp dir, --add-dir, Edit-allow rules), the security-critical deny-writes
    (settings.json, .claude/skills, bare-git files), and the network allow/deny
    + proxy config. The actual write roots are synthesized at call time by
    BashTool._maybe_wrap_sandbox."""
    return SandboxConfig()


def _settings_sandbox_bool(key: str, default: bool) -> bool:
    """Read a boolean settings.sandbox.<key> (default when absent). Best-effort —
    settings errors fall back to the default."""
    try:
        from hare.utils.settings.settings import get_initial_settings

        settings = get_initial_settings()
        sandbox = settings.get("sandbox") if isinstance(settings, dict) else None
        if isinstance(sandbox, dict) and key in sandbox:
            return bool(sandbox.get(key))
    except Exception:
        pass
    return default


def _settings_sandbox_enabled() -> bool:
    """Read settings.sandbox.enabled (default False). Best-effort."""
    return _settings_sandbox_bool("enabled", False)


def _sandbox_platform_supported() -> bool:
    """hare enforces only the macOS seatbelt path; treat other platforms as
    unsupported (Linux bubblewrap is not ported)."""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


# ---------------------------------------------------------------------------
# Command wrapping (OS-level enforcement)
#
# IMPORTANT SCOPE NOTE: 2.1.88 runs sandboxed bash via the external
# @anthropic-ai/sandbox-runtime package (bubblewrap on Linux, a seatbelt profile
# on macOS) with full network proxying — that profile is NOT in the recovered
# source, so it cannot be ported line-for-line. build_seatbelt_profile below is a
# hand-rolled WRITE-RESTRICTION approximation (no read restriction, NO network
# isolation). It is the achievable, testable slice on the user's platform and a
# no-op elsewhere. Sandboxing is OFF by default, so the normal Bash path is never
# touched.
# ---------------------------------------------------------------------------


def build_seatbelt_profile(cwd: str, allow_write: list[str] | None = None,
                           deny_write: list[str] | None = None) -> str:
    """Build a macOS seatbelt (sandbox-exec) profile string.

    Reads are unrestricted (`allow default`); writes are denied everywhere then
    re-allowed under the workspace + configured roots. Seatbelt uses last-match-
    wins, so a write under an allowed subpath is permitted while writes anywhere
    else fall through to the blanket deny. An explicit deny list is appended last
    so it overrides the allows."""
    allow_roots: list[str] = []

    def _add(p: str) -> None:
        if not p:
            return
        rp = os.path.realpath(p)
        if rp not in allow_roots:
            allow_roots.append(rp)

    _add(cwd)
    for p in allow_write or []:
        _add(p)

    def _esc(p: str) -> str:
        return p.replace("\\", "\\\\").replace('"', '\\"')

    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
    ]
    # Allow-roots are directory trees: (subpath ...). Device/pipe files a shell
    # needs are listed explicitly. (literal) is used for the single device files
    # purely for clarity — a (subpath "/dev/null") would match the same single
    # path; this is cosmetic, not a fix for a real denial. /dev/fd is added as a
    # (subpath) because the fd number varies (process substitution `>(...)`,
    # `tee /dev/fd/N`). /dev paths are kept raw (not realpath'd): /dev/stdout etc.
    # are symlinks and seatbelt matches the requested path, not the target.
    allow_block = [f'    (subpath "{_esc(p)}")' for p in allow_roots]
    allow_block.append('    (subpath "/dev/fd")')
    allow_block += [
        f'    (literal "{p}")'
        for p in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero")
    ]
    lines.append("(allow file-write*\n" + "\n".join(allow_block) + ")")
    denies = [os.path.realpath(p) for p in (deny_write or [])]
    if denies:
        deny_lines = "\n".join(f'    (subpath "{_esc(p)}")' for p in denies)
        lines.append(f"(deny file-write*\n{deny_lines})")
    return "\n".join(lines) + "\n"


def wrap_command_for_sandbox(
    argv: list[str], cwd: str, config: Optional[SandboxConfig] = None
) -> list[str]:
    """Wrap an exec argv so it runs under the OS sandbox, or return it unchanged.

    No-op (returns argv as-is) when: the config is missing/disabled, the platform
    is not macOS, or `sandbox-exec` is unavailable. This keeps the default Bash
    path byte-for-byte identical."""
    cfg = config or get_sandbox_config()
    if not cfg.enabled:
        return argv
    if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
        # Linux bubblewrap path is not ported; fail open rather than break Bash.
        return argv
    profile = build_seatbelt_profile(
        cwd, cfg.filesystem_allow_write, cfg.filesystem_deny_write
    )
    return ["sandbox-exec", "-p", profile, *argv]
