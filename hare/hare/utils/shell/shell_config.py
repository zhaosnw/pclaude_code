"""
Shell configuration detection.

Port of: src/utils/shell/shellConfig.ts

Detects the user's shell environment — including which shell they use, where
its binary lives, what config files (rc/profile) are loaded, and what the
startup sequence looks like — so other modules can tailor behavior to the
environment (prompt rendering, env var injection, quoting style, etc.).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Known shell families
# ---------------------------------------------------------------------------

ShellFamily = Literal[
    "bash",   # bash family (bash, sh compat)
    "zsh",    # zsh
    "fish",   # fish
    "csh",    # csh / tcsh
    "ksh",    # korn shell
    "dash",   # dash (Debian Almquist)
    "nu",     # nushell
    "xonsh",  # xonsh
    "elvish", # elvish
    "ion",    # ion
    "cmd",    # Windows cmd.exe
    "pwsh",   # PowerShell / pwsh
    "unknown",
]

# Ordered list of config files a login shell sources for each family.
# Each tuple is (filename, is_always_sourced).
_LOGIN_FILES: dict[ShellFamily, list[tuple[str, bool]]] = {
    "bash": [
        ("/etc/profile", True),
        ("~/.bash_profile", True),
        ("~/.bash_login", True),
        ("~/.profile", True),
        ("~/.bashrc", False),  # sourced by .bash_profile, not standalone
    ],
    "zsh": [
        ("/etc/zprofile", True),
        ("~/.zshenv", True),
        ("~/.zprofile", True),
        ("~/.zshrc", False),   # interactive only
        ("~/.zlogin", True),
    ],
    "fish": [
        ("/etc/fish/config.fish", True),
        ("~/.config/fish/config.fish", True),
    ],
    "csh": [
        ("/etc/csh.cshrc", True),
        ("~/.cshrc", True),
        ("~/.login", True),
    ],
    "ksh": [
        ("/etc/ksh.kshrc", True),
        ("~/.kshrc", True),
        ("~/.profile", True),
    ],
    "dash": [
        ("/etc/profile", True),
        ("~/.profile", True),
    ],
    "nu": [
        ("~/.config/nushell/config.nu", True),
        ("~/.config/nushell/env.nu", True),
    ],
    "xonsh": [
        ("~/.xonshrc", True),
    ],
    "elvish": [
        ("~/.config/elvish/rc.elv", True),
    ],
    "ion": [
        ("~/.config/ion/initrc", True),
    ],
    "cmd": [
        ("HKCU\\Software\\Microsoft\\Command Processor\\AutoRun", True),
    ],
    "pwsh": [
        ("~/.config/powershell/Microsoft.PowerShell_profile.ps1", True),
        (
            "~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
            True,
        ),
    ],
    "unknown": [],
}

# Editable / interactive rc files (for each family) that users most often customize.
_INTERACTIVE_RC: dict[ShellFamily, list[str]] = {
    "bash": ["~/.bashrc", "~/.bash_profile"],
    "zsh": ["~/.zshrc", "~/.zshenv"],
    "fish": ["~/.config/fish/config.fish"],
    "csh": ["~/.cshrc"],
    "ksh": ["~/.kshrc"],
    "dash": [],
    "nu": ["~/.config/nushell/config.nu"],
    "xonsh": ["~/.xonshrc"],
    "elvish": ["~/.config/elvish/rc.elv"],
    "ion": ["~/.config/ion/initrc"],
    "cmd": [],
    "pwsh": ["~/.config/powershell/Microsoft.PowerShell_profile.ps1"],
    "unknown": [],
}

# Shebang <-> family mapping.
_SHEBANG_TO_FAMILY: dict[str, ShellFamily] = {
    "sh": "dash",
    "dash": "dash",
    "bash": "bash",
    "zsh": "zsh",
    "fish": "fish",
    "csh": "csh",
    "tcsh": "csh",
    "ksh": "ksh",
    "nu": "nu",
    "xonsh": "xonsh",
    "elvish": "elvish",
    "ion": "ion",
    "pwsh": "pwsh",
    "powershell": "pwsh",
    "cmd": "cmd",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ShellConfig:
    """Resolved shell configuration for the current user/environment."""

    binary_path: str
    """Absolute path to the shell binary (e.g. /bin/zsh)."""

    basename: str
    """Just the binary name (e.g. zsh)."""

    family: ShellFamily
    """Shell family this binary belongs to."""

    version: str
    """Version string reported by the shell (empty if unavailable)."""

    login_files: list[str]
    """Absolute paths of rc/profile files that are sourced (resolved)."""

    interactive_rc: list[str]
    """Absolute paths of rc files the user is most likely to edit."""

    home: str
    """User HOME directory (resolved)."""

    env_var_syntax: str
    """Syntax for setting an environment variable one-liner ("export FOO=bar", etc.)."""

    join_separator: str
    """String used to join commands: ' && ' for bash-like, '; ' otherwise."""

    is_win_shell: bool
    """True if this is a Windows-native shell (cmd.exe / PowerShell)."""

    is_posix: bool
    """True if this is a POSIX-like shell."""

    is_login: bool
    """Is the current shell a login shell (best-effort heuristic)."""

    is_interactive: bool
    """Is the current shell interactive (best-effort heuristic)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expanduser(path: str) -> str:
    """Expand ~ to the home directory (cross-platform safe)."""
    return os.path.expanduser(path)


def _which(name: str) -> str | None:
    """Find a binary on PATH. Returns absolute path or None."""
    r = shutil.which(name)
    return os.path.abspath(r) if r else None


def _resolve_files(patterns: list[str]) -> list[str]:
    """Turn a list of path strings (may contain ~) into absolute resolved paths."""
    out: list[str] = []
    seen: set[str] = set()
    for p in patterns:
        resolved = _expanduser(p)
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def _exists(path: str) -> bool:
    """True when path is a real file on disk."""
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _detect_login_shell() -> bool:
    """Best-effort detection of a login shell.

    On macOS/Linux: checks if the process name starts with '-' (login shells).
    Also looks at $0, $. (zsh), and SHLVL.
    Falls back to checking for the presence of LOGIN or INVOCATION env vars.
    """
    # Classic convention: argv[0] starts with "-" for login shells.
    name0 = os.environ.get("_", "")
    if name0 and os.path.basename(name0).startswith("-"):
        return True

    # $0 may indicate login shell
    zero = os.environ.get("0", "")
    if zero.startswith("-"):
        return True

    # ZSH-specific: $- contains 'l' for login shells
    dash = os.environ.get("-", "")
    if "l" in dash:
        return True

    # Check if /proc/1/cmdline has a leading dash (Linux-only)
    try:
        with open("/proc/self/cmdline", "rb") as f:
            raw = f.read()
            if raw:
                argv0 = raw.split(b"\x00")[0]
                if argv0.startswith(b"-"):
                    return True
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return False


def _detect_interactive_shell() -> bool:
    """Best-effort detection of an interactive shell.

    Checks PS1 (set for interactive shells) and $- (contains 'i').
    """
    # $- containing 'i' is the standard interactive indicator
    dash = os.environ.get("-", "")
    if "i" in dash:
        return True

    # PS1 is usually only set in interactive shells
    if os.environ.get("PS1"):
        return True

    # stdin isatty check
    if sys.stdin.isatty():
        return True

    return False


# ---------------------------------------------------------------------------
# Family identification
# ---------------------------------------------------------------------------

def _identify_family(basename: str) -> ShellFamily:
    """Map a shell binary name to its family."""
    name = basename.lower().removesuffix(".exe")
    if name in ("bash",):
        return "bash"
    if name in ("zsh",):
        return "zsh"
    if name in ("fish",):
        return "fish"
    if name in ("csh", "tcsh"):
        return "csh"
    if name in ("ksh", "ksh93", "mksh", "oksh"):
        return "ksh"
    if name in ("dash", "ash"):
        return "dash"
    if name in ("nu", "nushell"):
        return "nu"
    if name in ("xonsh",):
        return "xonsh"
    if name in ("elvish",):
        return "elvish"
    if name in ("ion",):
        return "ion"
    if name in ("cmd",):
        return "cmd"
    if name in ("pwsh", "powershell"):
        return "pwsh"
    return "unknown"


def identify_shell_from_shebang(shebang: str) -> ShellFamily:
    """Given a shebang line (e.g. '#!/usr/bin/env bash'), return the family."""
    line = shebang.strip()
    if not line.startswith("#!"):
        return "unknown"
    interpreter = line[2:].strip()
    # Handle /usr/bin/env <shell>
    if "/env " in interpreter:
        parts = interpreter.split()
        interpreter = parts[-1] if parts else interpreter
    base = os.path.basename(interpreter)
    return _identify_family(base)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

_SHELL_VERSION_FLAGS: dict[ShellFamily, tuple[str, str]] = {
    "bash": ("--version", r"GNU bash.*?version (\S+)"),
    "zsh": ("--version", r"zsh (\S+)"),
    "fish": ("--version", r"fish,? version (\S+)"),
    "csh": ("--version", r""),
    "ksh": ("--version", r"version.*?(\S+)"),
    "dash": ("-c", r""),  # dash --version writes to stderr; use -c 'echo $0'.
    "nu": ("--version", r"(\d+\.\d+\.\d+)"),
    "xonsh": ("--version", r"xonsh/(\S+)"),
    "elvish": ("--version", r"\S+"),
    "ion": ("--version", r"\S+"),
    "cmd": ("/C", r""),  # handled separately
    "pwsh": ("-Command", r"\$PSVersionTable\.PSVersion\.ToString"),
    "unknown": ("--version", r""),
}

# Minimum version before fallback behaviour triggers.
RECOMMENDED_MIN_VERSIONS: dict[ShellFamily, tuple[int, ...]] = {
    "bash": (5, 0),
    "zsh": (5, 8),
    "fish": (3, 4),
    "ksh": (93,),
    "nu": (0, 80),
    "pwsh": (7, 0),
}


def _detect_version(binary: str, family: ShellFamily) -> str:
    """Try to get the shell version string via subprocess."""
    flag, regex = _SHELL_VERSION_FLAGS.get(family, ("--version", ""))

    if family == "cmd":
        # Query via PowerShell (cmd doesn't really have a --version).
        try:
            ver = os.environ.get("ComSpec", binary)
            return f"cmd@{ver}"
        except Exception:
            return ""

    # Build an invocation that prints version to stdout.
    if family == "pwsh":
        cmd_parts = [binary, "-NoProfile", "-NonInteractive", "-Command",
                     r"$PSVersionTable.PSVersion.ToString()"]
    elif family == "dash":
        # dash --version writes to stderr and returns non-zero.
        # We just echo the shell's internal version string.
        cmd_parts = [binary, "-c", 'echo "${0} version: $BASH_VERSION"']
    else:
        cmd_parts = [binary, flag]

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return ""

    # If the binary printed to stderr, prefer that for shells like dash.
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    lines = output.splitlines()
    first_line = lines[0] if lines else output

    if regex:
        m = re.search(regex, first_line, re.IGNORECASE)
        if m:
            return m.group(1)
        # Fallback: return entire first line (for elvish-like one-liners)
        return first_line.strip().split()[-1] if first_line.strip() else ""

    return first_line.strip()


def check_minimum_version(family: ShellFamily, version: str) -> bool:
    """Return True if `version` meets the RECOMMENDED_MIN_VERSIONS threshold."""
    req = RECOMMENDED_MIN_VERSIONS.get(family)
    if req is None:
        return True
    # Parse version tuple
    try:
        parts = re.split(r"[.+\-_]", version)
        actual = tuple(int(p) for p in parts if p.isdigit())
    except (ValueError, TypeError):
        return True  # unable to parse — assume OK
    return actual >= req


# ---------------------------------------------------------------------------
# Main resolve function
# ---------------------------------------------------------------------------

def resolve_shell_config(
    *,
    env: dict[str, str] | None = None,
    prefer_login: bool | None = None,
) -> ShellConfig:
    """Detect and return the full shell configuration for the current process.

    Parameters
    ----------
    env:
        Optional environment override dictionary (defaults to os.environ).
    prefer_login:
        Override the login-shell heuristic (True / False).  When *None* the
        function auto-detects.

    Returns
    -------
    ShellConfig
        Fully resolved shell configuration including binary, family, rc files,
        version, and UX hints.
    """
    _env = {**os.environ, **(env or {})}

    # ---- binary path -------------------------------------------------------
    shell = _env.get("SHELL", "")
    if shell and Path(shell).is_file():
        binary_path = os.path.abspath(shell)
    elif sys.platform == "win32":
        comspec = _env.get("ComSpec", os.path.join(
            _env.get("SystemRoot", r"C:\Windows"),
            "System32",
            "cmd.exe",
        ))
        binary_path = os.path.abspath(comspec)
    else:
        # Try to read from /etc/passwd, then fall back to /bin/sh.
        try:
            import pwd

            pw = pwd.getpwuid(os.getuid())
            binary_path = pw.pw_shell
        except Exception:
            binary_path = "/bin/sh"

    basename = os.path.basename(binary_path)
    family = _identify_family(basename)
    home = os.path.abspath(os.path.expanduser(_env.get("HOME", "~")))

    # ---- version -----------------------------------------------------------
    version = _detect_version(binary_path, family)

    # ---- rc / profile files ------------------------------------------------
    login_candidates: list[str] = []
    interactive_candidates: list[str] = []

    # Start with the standard list for this family.
    login_patterns = [f[0] for f in _LOGIN_FILES.get(family, [])]
    interactive_patterns = _INTERACTIVE_RC.get(family, [])

    # Append any extra files the env is explicitly pointing at.
    for var in (
        "BASH_ENV",
        "ENV",
        "ZSHRC",
        "ZDOTDIR",
        "BASH_PROFILE",
        "FISH_CONFIG",
    ):
        val = _env.get(var, "")
        if val and val not in login_patterns:
            login_patterns.append(val)

    login_files = _resolve_files(login_patterns)
    interactive_rc = _resolve_files(interactive_patterns)

    # ---- login / interactive -----------------------------------------------
    is_login = prefer_login if prefer_login is not None else _detect_login_shell()
    is_interactive = _detect_interactive_shell()

    # ---- env-var syntax and join separator ---------------------------------
    is_win = family in ("cmd", "pwsh")
    if family == "pwsh":
        env_var_syntax = "$env:{key} = '{value}'"
        join_separator = "; "
    elif family in ("cmd",):
        env_var_syntax = "set {key}={value}"
        join_separator = " & "
    elif family == "fish":
        env_var_syntax = "set -gx {key} '{value}'"
        join_separator = " && "
    elif family == "csh":
        env_var_syntax = "setenv {key} {value}"
        join_separator = " && "
    else:
        env_var_syntax = "export {key}='{value}'"
        join_separator = " && "

    return ShellConfig(
        binary_path=binary_path,
        basename=basename,
        family=family,
        version=version,
        login_files=login_files,
        interactive_rc=interactive_rc,
        home=home,
        env_var_syntax=env_var_syntax,
        join_separator=join_separator,
        is_win_shell=is_win,
        is_posix=not is_win,
        is_login=is_login,
        is_interactive=is_interactive,
    )


# ---------------------------------------------------------------------------
# Targeted convenience queries
# ---------------------------------------------------------------------------

def get_default_shell_path() -> str:
    """Return the absolute path to the current user's default shell."""
    return resolve_shell_config().binary_path


def get_shell_family() -> ShellFamily:
    """Return the family of the user's login shell."""
    return resolve_shell_config().family


def get_rc_files(*, existing_only: bool = True) -> list[str]:
    """Return the list of candidate rc/profile files.

    With *existing_only=True* (default) only files that actually exist on
    disk are returned.
    """
    cfg = resolve_shell_config()
    candidates = [*cfg.login_files, *cfg.interactive_rc]
    if existing_only:
        return [f for f in candidates if _exists(f)]
    return list(dict.fromkeys(candidates))  # deduplicate, preserve order


def get_env_set_command(key: str, value: str) -> str:
    """Build a shell-appropriate one-liner that sets an environment variable.

    >>> get_env_set_command("EDITOR", "vim")
    "export EDITOR='vim'"
    """
    cfg = resolve_shell_config()
    return cfg.env_var_syntax.format(key=key, value=value)


def get_join_command(*commands: str) -> str:
    """Join multiple shell commands with the appropriate separator.

    >>> get_join_command("cd /tmp", "ls -la")
    "cd /tmp && ls -la"
    """
    cfg = resolve_shell_config()
    sep = cfg.join_separator
    return sep.join(commands)
