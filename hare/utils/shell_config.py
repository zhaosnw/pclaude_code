"""Shell configuration file helpers — alias and PATH management.

Port of: frontend/src/utils/shellConfig.ts

Manages hare shell aliases and PATH entries in user shell configuration files
(.bashrc, .zshrc, config.fish, etc.).  Provides discovery, filtering, safe
add/remove/update operations with backup and atomic-write guarantees.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from hare.utils.errors import is_fs_inaccessible

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

HARE_ALIAS_REGEX = re.compile(r"^\s*alias\s+hare\s*=")
"""Matches lines that define a ``hare`` shell alias (bash/zsh/fish-style)."""

# Backward-compatible name (TS port artifact).
CLAUDE_ALIAS_REGEX = HARE_ALIAS_REGEX

# Pattern for extracting the alias target from a matched line.
_ALIAS_TARGET_PATTERNS: list[re.Pattern[str]] = [
    # Quoted variants (single or double).
    re.compile(r"""alias\s+hare\s*=\s*["']([^"']+)["']"""),
    # Unquoted variant — captures until end-of-line or comment.
    re.compile(r"alias\s+hare\s*=\s*([^#\n]+)"),
]

# Pattern for fish-style ``alias hare /path/to/hare`` (no =).
_FISH_ALIAS_REGEX = re.compile(r"^\s*alias\s+hare\s+(\S+)")

# Pattern for a PATH export line (POSIX shells).
_PATH_EXPORT_REGEX = re.compile(
    r"""^\s*(?:export\s+)?PATH\s*=\s*["']?([^"'\n]+)["']?"""
)

# Pattern for fish-style ``set -gx PATH ...``.
_FISH_PATH_REGEX = re.compile(
    r"""^\s*set\s+-gx\s+PATH\s+(.+)"""
)

# ---------------------------------------------------------------------------
# Shell config file discovery
# ---------------------------------------------------------------------------

# Map of shell name → config file paths (relative to home or ZDOTDIR).
_SHELL_CONFIG_MAP: dict[str, list[str]] = {
    "zsh": [".zshrc", ".zshenv", ".zprofile"],
    "bash": [".bashrc", ".bash_profile", ".bash_login", ".profile"],
    "fish": [".config/fish/config.fish"],
    "csh": [".cshrc", ".login"],
    "tcsh": [".tcshrc", ".cshrc", ".login"],
    "ksh": [".kshrc", ".profile"],
    "dash": [".profile"],
    "nu": [".config/nushell/config.nu", ".config/nushell/env.nu"],
    "xonsh": [".xonshrc"],
    "elvish": [".config/elvish/rc.elv"],
    "ion": [".config/ion/initrc"],
    "pwsh": [
        ".config/powershell/Microsoft.PowerShell_profile.ps1",
        "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
    ],
}

# Shells where alias commands use the POSIX ``alias name=value`` syntax.
_ALIAS_COMPATIBLE_SHELLS: set[str] = {
    "zsh", "bash", "fish", "csh", "tcsh", "ksh", "dash", "xonsh",
}

# Shells where PATH is managed with POSIX ``export PATH=...`` syntax.
_PATH_EXPORT_SHELLS: set[str] = {
    "zsh", "bash", "ksh", "dash", "xonsh",
}


def _expand_home(path: str, home: str) -> str:
    """Expand a leading ``~`` in *path* to *home*."""
    if path.startswith("~/"):
        return home + path[1:]
    if path == "~":
        return home
    return path


def _get_shell_from_env(
    env: dict[str, str | None] | None = None,
) -> str:
    """Guess the current shell name from the SHELL env var.

    Returns "unknown" when the shell cannot be determined.
    """
    _env = env if env is not None else dict(os.environ)
    shell = _env.get("SHELL", "") or ""
    base = os.path.basename(shell).lower()
    # Map common binary names to shell keys.
    known = {
        "bash": "bash", "zsh": "zsh", "fish": "fish",
        "csh": "csh", "tcsh": "tcsh", "ksh": "ksh",
        "ksh93": "ksh", "mksh": "ksh", "dash": "dash",
        "nu": "nu", "nushell": "nu", "xonsh": "xonsh",
        "elvish": "elvish", "ion": "ion",
        "pwsh": "pwsh", "powershell": "pwsh",
    }
    return known.get(base, "unknown")


# ---------------------------------------------------------------------------
# Path getters
# ---------------------------------------------------------------------------


def get_local_hare_path() -> str:
    """Installer default alias target (``~/.hare/local/hare``)."""
    return str(Path.home() / ".hare" / "local" / "hare")


def get_shell_config_paths(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
    existing_only: bool = False,
) -> dict[str, list[str]]:
    """Return a mapping of shell name → list of candidate config file paths.

    When *shells* is ``None`` the current shell is auto-detected via ``$SHELL``
    and a few common fallback shells (zsh, bash, fish) are included so that
    alias management covers the most likely config files.

    When *existing_only* is ``True`` only files that actually exist on disk
    are returned.

    Respects ``$ZDOTDIR`` for zsh users (zsh looks for ``.zshrc`` there).
    """
    home = homedir or str(Path.home())
    _env = env if env is not None else dict(os.environ)

    # Determine zsh config directory (ZDOTDIR or home).
    zdot = (_env.get("ZDOTDIR") or home) if isinstance(_env, dict) else home

    if shells is None:
        detected = _get_shell_from_env(env=_env)
        candidates = [detected] if detected != "unknown" else []
        # Always include bash and zsh so we catch common setups.
        for extra in ("zsh", "bash", "fish"):
            if extra not in candidates:
                candidates.append(extra)
        shells = candidates

    result: dict[str, list[str]] = {}
    for shell in shells:
        templates = _SHELL_CONFIG_MAP.get(shell, [])
        paths: list[str] = []
        for tmpl in templates:
            if shell == "zsh" and tmpl.startswith("."):
                # ZDOTDIR controls where zsh dotfiles live.
                paths.append(str(Path(zdot) / tmpl))
            elif tmpl.startswith("."):
                paths.append(str(Path(home) / tmpl))
            else:
                # Relative path under home (fish, nu, etc.).
                paths.append(str(Path(home) / tmpl))

        if existing_only:
            paths = [p for p in paths if Path(p).is_file()]

        if paths:
            result[shell] = paths

    return result


def get_existing_shell_configs(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
) -> list[str]:
    """Return a flat, deduplicated list of shell config files that exist."""
    mapping = get_shell_config_paths(
        env=env, homedir=homedir, shells=shells, existing_only=True,
    )
    seen: set[str] = set()
    result: list[str] = []
    for paths in mapping.values():
        for p in paths:
            if p not in seen:
                seen.add(p)
                result.append(p)
    return result


# ---------------------------------------------------------------------------
# Async file I/O helpers
# ---------------------------------------------------------------------------


async def read_file_lines(
    file_path: str,
    *,
    encoding: str = "utf-8",
    fallback_encoding: str | None = "latin-1",
) -> list[str] | None:
    """Read a file and return its lines (without trailing newlines).

    Returns ``None`` when the file does not exist / is inaccessible (matching
    the ``is_fs_inaccessible`` contract).  Raises on other errors.

    The ``encoding`` attempts ``utf-8`` first; when *fallback_encoding* is set
    and a ``UnicodeDecodeError`` occurs the file is re-read with that encoding.
    """
    def _read() -> list[str] | None:
        p = Path(file_path)
        try:
            raw = p.read_bytes()
        except OSError as e:
            if is_fs_inaccessible(e):
                return None
            raise

        decoded: str | None = None
        for enc in (encoding, fallback_encoding):
            if enc is None:
                continue
            try:
                decoded = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if decoded is None and fallback_encoding is None:
            raise ValueError(
                f"Could not decode {file_path!r} with encoding {encoding!r}"
            )

        if decoded is None:
            decoded = raw.decode(encoding)  # let the error propagate

        # Normalise line endings (CRLF → LF) and split.
        text = decoded.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        # Drop a single trailing empty line (common for POSIX text files).
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    return await asyncio.to_thread(_read)


async def write_file_lines(
    file_path: str,
    lines: list[str],
    *,
    encoding: str = "utf-8",
    atomic: bool = True,
) -> None:
    """Write *lines* to *file_path*, joined by ``\\n`` with a trailing newline.

    When *atomic* is ``True`` (the default) the write uses a temp-file +
    rename strategy so the file is never left in a partially-written state.

    Missing parent directories are created automatically.
    """
    content = "\n".join(lines)
    if content:
        content += "\n"

    def _write() -> None:
        dest = Path(file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if not atomic:
            dest.write_text(content, encoding=encoding)
            return

        # Atomic write: temp file in same directory, then rename.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(dest.parent),
            prefix="." + dest.name + ".",
            suffix=".tmp",
        )
        try:
            os.write(fd, content.encode(encoding))
            os.fsync(fd)
            os.close(fd)
            fd = -1
            # Preserve existing permissions if possible.
            if dest.exists():
                shutil.copymode(str(dest), tmp_name)
            os.replace(tmp_name, str(dest))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
            raise

    await asyncio.to_thread(_write)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def _backup_path(file_path: str) -> str:
    """Return the backup file path for *file_path*."""
    return file_path + ".hare.bak"


async def backup_shell_config(file_path: str) -> bool:
    """Create a timestamped backup of *file_path* if it exists.

    Returns ``True`` if a backup was created, ``False`` if the source does not
    exist.
    """
    def _backup() -> bool:
        src = Path(file_path)
        if not src.is_file():
            return False
        bak = Path(_backup_path(file_path))
        shutil.copy2(str(src), str(bak))
        return True

    return await asyncio.to_thread(_backup)


async def restore_shell_config(file_path: str) -> bool:
    """Restore *file_path* from its most recent backup.

    Returns ``True`` if the restore succeeded, ``False`` if no backup exists.
    """
    def _restore() -> bool:
        bak = Path(_backup_path(file_path))
        if not bak.is_file():
            return False
        shutil.copy2(str(bak), str(file_path))
        return True

    return await asyncio.to_thread(_restore)


async def remove_backup(file_path: str) -> bool:
    """Delete the backup for *file_path*.

    Returns ``True`` if the backup was deleted, ``False`` if it did not exist.
    """
    def _rm() -> bool:
        bak = Path(_backup_path(file_path))
        if not bak.is_file():
            return False
        bak.unlink()
        return True

    return await asyncio.to_thread(_rm)


# ---------------------------------------------------------------------------
# Alias extraction helpers
# ---------------------------------------------------------------------------

def _extract_alias_target(line: str) -> str | None:
    """Try every known alias pattern against *line* and return the target.

    Returns ``None`` when *line* is not a hare alias or the target cannot be
    extracted.
    """
    # POSIX-style (bash/zsh/korn).
    for pat in _ALIAS_TARGET_PATTERNS:
        m = pat.search(line)
        if m:
            return m.group(1).strip()
    # fish-style: ``alias hare /path/to/hare``
    m = _FISH_ALIAS_REGEX.search(line)
    if m:
        return m.group(1).strip()
    return None


def _is_hare_alias_line(line: str) -> bool:
    """Return ``True`` when *line* looks like a hare alias definition."""
    return bool(HARE_ALIAS_REGEX.search(line))


# ---------------------------------------------------------------------------
# Alias filtering
# ---------------------------------------------------------------------------


def filter_hare_aliases(
    lines: list[str],
    *,
    target: str | None = None,
    remove_all: bool = False,
) -> tuple[list[str], bool]:
    """Filter hare aliases from a list of lines.

    By default only removes aliases whose target matches
    :func:`get_local_hare_path` (the installer default).  Pass *target* to
    match against a different path, or *remove_all=True* to strip every hare
    alias line regardless of its target.

    Returns ``(filtered_lines, had_alias)`` — *had_alias* is ``True`` when at
    least one matching alias line was removed.
    """
    had_alias = False
    local_path = target or get_local_hare_path()
    filtered: list[str] = []

    for line in lines:
        if not _is_hare_alias_line(line):
            filtered.append(line)
            continue

        if remove_all:
            had_alias = True
            continue

        alias_target = _extract_alias_target(line)
        if alias_target is not None and alias_target == local_path:
            had_alias = True
            continue

        # Keep the line — it is a hare alias, but for a different (user) target.
        filtered.append(line)

    return filtered, had_alias


# ---------------------------------------------------------------------------
# Alias discovery
# ---------------------------------------------------------------------------


async def find_hare_alias(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
) -> str | None:
    """Scan shell config files for a hare alias and return its target.

    Checks configs in order (current shell first, then bash, then fish) and
    returns the first match found, or ``None`` when no alias is configured.
    """
    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue
            for line in lines:
                if _is_hare_alias_line(line):
                    target = _extract_alias_target(line)
                    if target:
                        return target
    return None


async def find_valid_hare_alias(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
) -> str | None:
    """Like :func:`find_hare_alias`, but only returns the target when the
    alias *points to a file that actually exists on disk* (after expanding
    ``~``).
    """
    alias_target = await find_hare_alias(env=env, homedir=homedir, shells=shells)
    if not alias_target:
        return None

    home = homedir or str(Path.home())
    expanded = _expand_home(alias_target, home)

    def _check() -> bool:
        p = Path(expanded)
        try:
            return p.is_file() or p.is_symlink()
        except OSError:
            return False

    if await asyncio.to_thread(_check):
        return alias_target
    return None


async def has_hare_alias(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> bool:
    """Return ``True`` when any shell config contains a hare alias."""
    target = await find_hare_alias(env=env, homedir=homedir)
    return target is not None


async def list_hare_aliases(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
) -> list[tuple[str, str, str]]:
    """Return all hare aliases found in shell configs.

    Each entry is ``(shell_name, config_file_path, alias_target)``.
    """
    results: list[tuple[str, str, str]] = []
    configs = get_shell_config_paths(env=env, homedir=homedir)

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue
            for line in lines:
                if _is_hare_alias_line(line):
                    target = _extract_alias_target(line)
                    if target:
                        results.append((shell_name, path, target))
    return results


# ---------------------------------------------------------------------------
# Alias management (add / remove / update)
# ---------------------------------------------------------------------------


async def add_hare_alias(
    alias_path: str,
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
    force: bool = False,
    backup: bool = True,
) -> bool:
    """Add a ``hare`` alias to the user's shell configuration.

    Appends ``alias hare='<alias_path>'`` to the first writable config file
    for the detected shell (or to ``~/.bashrc`` as a fallback).  If a hare
    alias already exists it is left untouched unless *force* is ``True`` (in
    which case the existing alias is replaced).

    When *backup* is ``True`` a ``.hare.bak`` copy is created before
    modifying the file.

    Returns ``True`` when the alias was added / updated.
    """
    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)

    # Find the first writable config (prefer the primary shell).
    target_path: str | None = None
    target_shell: str | None = None
    for shell_name in configs:
        for path in configs[shell_name]:
            try:
                p = Path(path)
                if p.is_file():
                    if os.access(str(p), os.W_OK):
                        target_path = path
                        target_shell = shell_name
                        break
                else:
                    # File doesn't exist yet — check parent dir is writable.
                    if os.access(str(p.parent), os.W_OK):
                        target_path = path
                        target_shell = shell_name
                        break
            except OSError:
                continue
        if target_path:
            break

    if target_path is None:
        # Fallback: try to create ~/.bashrc or use it.
        home = homedir or str(Path.home())
        fallback = str(Path(home) / ".bashrc")
        try:
            Path(fallback).parent.mkdir(parents=True, exist_ok=True)
            if not Path(fallback).exists() or os.access(fallback, os.W_OK):
                target_path = fallback
        except OSError:
            return False

    if target_path is None:
        return False

    # Build the alias line with shell-appropriate syntax.
    if target_shell == "fish":
        alias_line = f"alias hare {alias_path}"
    elif target_shell in ("csh", "tcsh"):
        alias_line = f"alias hare {alias_path}"
    else:
        alias_line = f"alias hare='{alias_path}'"

    # Read existing lines.
    lines = await read_file_lines(target_path) or []

    if force:
        # Remove all existing hare aliases from this file.
        lines, _ = filter_hare_aliases(lines, remove_all=True)

    # Check if alias already exists.
    existing = any(_is_hare_alias_line(ln) for ln in lines)
    if existing and not force:
        return False  # Already present, not forcing.

    if backup:
        await backup_shell_config(target_path)

    # Append the new alias (ensure it's on its own "paragraph" if needed).
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(alias_line)

    await write_file_lines(target_path, lines)
    return True


async def remove_hare_aliases(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
    target: str | None = None,
    backup: bool = True,
) -> int:
    """Remove hare aliases from all discovered shell config files.

    By default only removes aliases pointing to :func:`get_local_hare_path`.
    Pass *target* to remove aliases pointing to a specific path, or
    ``target=""`` to remove every hare alias regardless of target.

    When *backup* is ``True`` each modified file is backed up first
    (``.hare.bak``).

    Returns the number of files that were modified.
    """
    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)
    modified = 0

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue

            # Determine removal mode.
            if target == "" or target is None and get_local_hare_path() == "":
                filtered, had = filter_hare_aliases(lines, remove_all=True)
            else:
                filtered, had = filter_hare_aliases(lines, target=target)

            if not had:
                continue

            if backup:
                await backup_shell_config(path)
            await write_file_lines(path, filtered)
            modified += 1

    return modified


async def update_hare_alias(
    new_target: str,
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
    backup: bool = True,
) -> int:
    """Update the target of all hare aliases to *new_target*.

    When no existing hare alias is found, one is added (behaves like
    ``add_hare_alias(force=True)``).

    Returns the number of files modified.
    """
    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)
    modified = 0

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue

            had_hare = False
            shell = shell_name

            new_lines: list[str] = []
            for line in lines:
                if _is_hare_alias_line(line):
                    had_hare = True
                    # Replace with the new alias.
                    if shell == "fish":
                        new_lines.append(f"alias hare {new_target}")
                    elif shell in ("csh", "tcsh"):
                        new_lines.append(f"alias hare {new_target}")
                    else:
                        new_lines.append(f"alias hare='{new_target}'")
                else:
                    new_lines.append(line)

            if not had_hare:
                continue

            if backup:
                await backup_shell_config(path)
            await write_file_lines(path, new_lines)
            modified += 1

    if modified == 0:
        # No alias existed — add one.
        added = await add_hare_alias(
            new_target, env=env, homedir=homedir, shells=shells,
            force=True, backup=backup,
        )
        return 1 if added else 0

    return modified


# ---------------------------------------------------------------------------
# PATH management
# ---------------------------------------------------------------------------


async def _find_path_line_index(
    lines: list[str], shell: str
) -> int | None:
    """Return the index of the PATH-setting line, or ``None``."""
    for i, line in enumerate(lines):
        if shell == "fish":
            if _FISH_PATH_REGEX.search(line):
                return i
        else:
            if _PATH_EXPORT_REGEX.search(line):
                return i
    return None


async def has_hare_in_path(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
) -> bool:
    """Check whether the hare local directory is in PATH (via shell configs)."""
    local_dir = str(Path(get_local_hare_path()).parent)
    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue
            for line in lines:
                if _PATH_EXPORT_REGEX.search(line) or _FISH_PATH_REGEX.search(line):
                    if local_dir in line:
                        return True
    return False


async def ensure_hare_in_path(
    *,
    env: dict[str, str | None] | None = None,
    homedir: str | None = None,
    shells: list[str] | None = None,
    backup: bool = True,
) -> bool:
    """Ensure the hare local directory is in the user's PATH via their shell config.

    Appends an ``export PATH="...:$PATH"`` (or fish equivalent) to the first
    writable config that does not already contain the entry.

    Returns ``True`` when the config was updated or the entry was already present.
    When no writable config exists, returns ``False``.
    """
    local_dir = str(Path(get_local_hare_path()).parent)

    # Quick check — already present?
    if await has_hare_in_path(env=env, homedir=homedir, shells=shells):
        return True

    configs = get_shell_config_paths(env=env, homedir=homedir, shells=shells)

    for shell_name in configs:
        for path in configs[shell_name]:
            lines = await read_file_lines(path)
            if lines is None:
                continue

            # Append the export.
            if shell_name == "fish":
                entry = f"set -gx PATH {local_dir} $PATH"
            elif shell_name in ("csh", "tcsh"):
                entry = f"setenv PATH {local_dir}:$PATH"
            else:
                entry = f'export PATH="{local_dir}:$PATH"'

            if backup:
                await backup_shell_config(path)

            if lines and lines[-1] != "":
                lines.append("")
            lines.append(entry)
            await write_file_lines(path, lines)
            return True

    return False


async def get_path_entries(
    file_path: str,
    *,
    env: dict[str, str | None] | None = None,
) -> list[str]:
    """Parse PATH entries from a shell config file.

    Scans for ``export PATH=...`` or ``set -gx PATH ...`` lines and returns
    the colon-separated directory entries.  Handles ``$VAR``-style variable
    references (they are left as-is).
    """
    lines = await read_file_lines(file_path)
    if lines is None:
        return []

    shell = _get_shell_from_env(env=env)
    for line in lines:
        m = _PATH_EXPORT_REGEX.search(line) or _FISH_PATH_REGEX.search(line)
        if m:
            raw = m.group(1).strip()
            # Strip surrounding quotes.
            if raw and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                raw = raw[1:-1]
            return [entry.strip() for entry in raw.split(":") if entry.strip()]

    return []


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------


async def append_to_shell_config(
    file_path: str,
    content: str,
    *,
    backup: bool = True,
    ensure_newline_separation: bool = True,
) -> bool:
    """Safely append *content* to a shell config file.

    Creates the file (and parent directories) if it does not exist.

    Returns ``True`` on success.
    """
    try:
        lines = await read_file_lines(file_path) or []

        if backup:
            await backup_shell_config(file_path)

        if ensure_newline_separation and lines and lines[-1] != "":
            lines.append("")

        for entry_line in content.splitlines():
            lines.append(entry_line)

        await write_file_lines(file_path, lines)
        return True
    except OSError:
        return False


async def safe_modify_config(
    file_path: str,
    modifier,
    *,
    backup: bool = True,
) -> bool:
    """Read *file_path*, pass lines to ``modifier(lines) -> list[str]``, and
    write back atomically.

    When *backup* is ``True`` a ``.hare.bak`` copy is created before writing.

    Returns ``True`` on success.
    """
    try:
        lines = await read_file_lines(file_path) or []
        if backup:
            await backup_shell_config(file_path)
        new_lines = modifier(lines)
        if new_lines is lines:
            return True  # in-place modification, no write needed.
        await write_file_lines(file_path, new_lines)
        return True
    except OSError:
        return False
