"""
Project-scoped cache directory paths (`cachePaths.ts`).

Port of: src/utils/cachePaths.ts

Provides deterministic, sanitized directory paths for the ``claude-cli``
cache tree. Directory names are stable across upgrades (using djb2 hash,
not wyhash) so that existing cache data (error logs, MCP logs, messages)
is never orphaned after a binary update.

Typical usage::

    from hare.utils.cache_paths import CACHE_PATHS

    error_dir = CACHE_PATHS.errors()
    mcp_dir = CACHE_PATHS.mcp_logs("my-server")

    # One-time setup (idempotent)
    CACHE_PATHS.ensure_dirs()

    # List *.jsonl files in errors dir, newest first
    for p in CACHE_PATHS.list_files(CACHE_PATHS.errors(), pattern="*.jsonl"):
        print(p)

    # Prune log files older than 30 days
    removed = CACHE_PATHS.prune_old_files(CACHE_PATHS.errors(), max_age_days=30)
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Optional

from hare.utils.hash import djb2_hash


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SANITIZED_LENGTH = 200

# Windows drive-letter regex (used to strip C:, D:, etc.)
_DRIVE_LETTER_RE = re.compile(r"^[a-zA-Z]:\\?")

# Characters that are unsafe in directory names across platforms.
# We replace them with a single hyphen.
_UNSAFE_PATH_CHARS_RE = re.compile(r"[^a-zA-Z0-9]")


# =============================================================================
# Private helpers
# =============================================================================


def _sanitize_path(name: str) -> str:
    """Sanitize *name* into a safe directory segment.

    Uses **djb2** (not wyhash / Bun.hash) so that cache directory names
    remain stable across upgrades.  When the sanitized string exceeds
    :data:`MAX_SANITIZED_LENGTH` we append a hex digest of the djb2 hash to
    guarantee uniqueness.

    Parameters
    ----------
    name : str
        Raw path string (e.g. ``os.getcwd()``).

    Returns
    -------
    str
        Safe directory name.

    Edge cases
    ----------
    - ``None``, ``""``, or whitespace-only → ``"root"``.
    - Leading ``/`` and ``\\`` are stripped so we never produce names
      starting with ``-``.
    - Windows drive letters (``C:\\``) are stripped.
    """
    if not name or not name.strip():
        return "root"

    # Normalize separators and strip Windows drive letter
    cleaned = name.replace("\\", "/")
    cleaned = _DRIVE_LETTER_RE.sub("", cleaned)

    # Remove leading / and repeated slashes
    cleaned = cleaned.lstrip("/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")

    sanitized = _UNSAFE_PATH_CHARS_RE.sub("-", cleaned)

    # Collapse consecutive hyphens
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")

    sanitized = sanitized.strip("-")

    if not sanitized:
        return "root"

    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized

    h = abs(djb2_hash(name))
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{h:x}"


def _env_paths_cache() -> str:
    """Return the base cache directory for ``claude-cli``.

    Prefers ``platformdirs``; falls back to ``~/.cache/claude-cli`` (Linux),
    ``~/Library/Caches/claude-cli`` (macOS), or
    ``%LOCALAPPDATA%\\claude-cli\\Cache`` (Windows).

    Cached via ``@lru_cache(maxsize=1)`` — call :func:`refresh_env_cache`
    to invalidate (e.g. after ``os.environ`` changes).
    """
    try:
        import platformdirs

        return platformdirs.user_cache_dir("claude-cli", appauthor=False)
    except ImportError:
        return _fallback_cache_dir()


def _fallback_cache_dir() -> str:
    """Platform-aware fallback when ``platformdirs`` is not installed."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "claude-cli", "Cache")
    elif sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"), "Library", "Caches", "claude-cli"
        )
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            return os.path.join(xdg, "claude-cli")
        return os.path.join(os.path.expanduser("~"), ".cache", "claude-cli")


def _env_config_dir() -> str:
    """Return the base config directory for ``claude-cli``."""
    try:
        import platformdirs

        return platformdirs.user_config_dir("claude-cli", appauthor=False)
    except ImportError:
        if sys.platform == "win32":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            return os.path.join(base, "claude-cli", "Config")
        elif sys.platform == "darwin":
            return os.path.join(
                os.path.expanduser("~"), "Library", "Application Support", "claude-cli"
            )
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            if xdg:
                return os.path.join(xdg, "claude-cli")
            return os.path.join(os.path.expanduser("~"), ".config", "claude-cli")


def _env_data_dir() -> str:
    """Return the base data directory for ``claude-cli``."""
    try:
        import platformdirs

        return platformdirs.user_data_dir("claude-cli", appauthor=False)
    except ImportError:
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
            return os.path.join(base, "claude-cli", "Data")
        elif sys.platform == "darwin":
            return os.path.join(
                os.path.expanduser("~"), "Library", "Application Support", "claude-cli"
            )
        else:
            xdg = os.environ.get("XDG_DATA_HOME")
            if xdg:
                return os.path.join(xdg, "claude-cli")
            return os.path.join(
                os.path.expanduser("~"), ".local", "share", "claude-cli"
            )


def _env_temp_dir() -> str:
    """Return a suitable temp directory for ``claude-cli``."""
    import tempfile

    base = tempfile.gettempdir()
    return os.path.join(base, "claude-cli")


def _project_dir(cwd: str | None = None) -> str:
    """Sanitize *cwd* (or ``os.getcwd()``) to a stable directory name."""
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = os.path.expanduser("~")
    return _sanitize_path(cwd)


# =============================================================================
# Refresh helpers (exposed for env-variable aware callers)
# =============================================================================

# We wrap the cached function so we can clear it on demand.
_ENV_CACHE_FN = lru_cache(maxsize=1)(_env_paths_cache)


def _get_cache_base() -> str:
    """Return the cached base cache directory."""
    return _ENV_CACHE_FN()


def refresh_env_cache() -> None:
    """Invalidate the internal lru_cache for environment paths.

    Call this after mutating environment variables that affect path resolution
    (e.g. ``XDG_CACHE_HOME``, ``LOCALAPPDATA``).
    """
    _ENV_CACHE_FN.cache_clear()


# =============================================================================
# CachePaths
# =============================================================================


class CachePaths:
    """Namespace of static path builders for the ``claude-cli`` cache tree.

    All path-accessor methods are ``@staticmethod`` so the class can be used
    either via the module-level alias::

        CACHE_PATHS.errors()

    or via import::

        from hare.utils.cache_paths import CachePaths
        CachePaths.errors()

    Utility methods like :meth:`ensure_dirs`, :meth:`list_files`, and
    :meth:`prune_old_files` operate on the file system; path-accessor
    methods are pure string/Path constructors.
    """

    # -- path accessors (no I/O) ------------------------------------------

    @staticmethod
    def base_logs() -> str:
        """Base directory for all project-scoped cache logs.

        Equivalent to TS: ``join(paths.cache, getProjectDir(cwd))``.
        """
        base = _get_cache_base()
        proj = _project_dir()
        return os.path.join(base, proj)

    @staticmethod
    def errors() -> str:
        """Directory for session error logs (``*.jsonl``)."""
        return os.path.join(CachePaths.base_logs(), "errors")

    @staticmethod
    def messages() -> str:
        """Directory for session message / transcript logs (``*.jsonl``)."""
        return os.path.join(CachePaths.base_logs(), "messages")

    @staticmethod
    def mcp_logs(server_name: str) -> str:
        """Directory for MCP server log files.

        The *server_name* is sanitized for cross-platform safety (colons,
        slashes, etc. are replaced with hyphens).
        """
        if not server_name or not server_name.strip():
            raise ValueError("server_name must be a non-empty string")
        safe = _sanitize_path(server_name)
        return os.path.join(CachePaths.base_logs(), f"mcp-logs-{safe}")

    # -- extended path accessors ------------------------------------------

    @staticmethod
    def base_config() -> str:
        """Base config directory for ``claude-cli``.

        Use this for persistent configuration data (settings, hooks,
        keybindings) that should survive cache-clears.
        """
        return _env_config_dir()

    @staticmethod
    def base_data() -> str:
        """Base data directory for ``claude-cli``.

        Use this for user-level data files (memories, pastes, skill data)
        that should survive cache-clears but are not config.
        """
        return _env_data_dir()

    @staticmethod
    def base_temp() -> str:
        """Session-safe temp directory for ``claude-cli``.

        Content here is fair game for OS-level cleanup.  Prefer this over
        ``tempfile.gettempdir()`` directly so all ``claude-cli`` temp
        artefacts live under one root.
        """
        return _env_temp_dir()

    @staticmethod
    def project_config(cwd: str | None = None) -> str:
        """Project-scoped config directory (stored under *base_config*)."""
        base = CachePaths.base_config()
        proj = _project_dir(cwd)
        return os.path.join(base, proj)

    @staticmethod
    def project_data(cwd: str | None = None) -> str:
        """Project-scoped data directory (stored under *base_data*)."""
        base = CachePaths.base_data()
        proj = _project_dir(cwd)
        return os.path.join(base, proj)

    # -- I/O convenience methods ------------------------------------------

    @staticmethod
    def ensure_dirs(*, mode: int = 0o700) -> dict[str, str]:
        """Create every known cache directory (idempotent).

        Creates (if missing):
        - ``errors``
        - ``messages``
        - ``base_logs`` (project-scoped base)
        - ``base_config`` (global)
        - ``base_data`` (global)
        - ``base_temp``

        Parameters
        ----------
        mode : int
            Directory permission bits (default: ``0o700``).

        Returns
        -------
        dict[str, str]
            Mapping of directory *role* → absolute path.  Directories that
            could not be created (permissions, disk-full) are still listed
            but logged as failures.
        """
        dirs: dict[str, str] = {}
        roles: list[tuple[str, str]] = [
            ("base_logs", CachePaths.base_logs()),
            ("errors", CachePaths.errors()),
            ("messages", CachePaths.messages()),
            ("base_config", CachePaths.base_config()),
            ("base_data", CachePaths.base_data()),
            ("base_temp", CachePaths.base_temp()),
        ]

        for role, path in roles:
            dirs[role] = path
            _mkdir_p(path, mode=mode)

        return dirs

    @staticmethod
    def ensure(path: str, *, mode: int = 0o700) -> str:
        """Ensure a single directory exists (idempotent).

        Returns *path* unchanged so callers can chain.
        """
        _mkdir_p(path, mode=mode)
        return path

    @staticmethod
    def list_files(path: str, *, pattern: str = "*") -> list[Path]:
        """List files inside *path* matching *pattern* (fnmatch).

        Returns a list of :class:`pathlib.Path` objects sorted by
        modification time descending (newest first).  Does **not** recurse.

        If *path* does not exist or is unreadable, returns an empty list.
        """
        p = Path(path)
        try:
            if not p.is_dir():
                return []
            entries = sorted(
                (e for e in p.iterdir() if e.is_file() and fnmatch(e.name, pattern)),
                key=lambda e: e.stat().st_mtime,
                reverse=True,
            )
            return entries
        except OSError:
            return []

    @staticmethod
    def prune_old_files(
        path: str,
        *,
        max_age_days: int = 30,
        pattern: str = "*",
        dry_run: bool = False,
    ) -> list[str]:
        """Remove files in *path* older than *max_age_days*.

        Parameters
        ----------
        path : str
            Directory to scan (non-recursive).
        max_age_days : int
            Files with mtime more than this many days ago are removed.
        pattern : str
            fnmatch pattern to filter candidates (default ``"*"``).
        dry_run : bool
            If ``True``, compute the removal list but do not delete.

        Returns
        -------
        list[str]
            Absolute paths of files that were removed (or would be removed
            in dry-run mode).
        """
        cutoff = time.time() - (max_age_days * 86400)
        removed: list[str] = []

        p = Path(path)
        if not p.is_dir():
            return removed

        for entry in p.iterdir():
            if not entry.is_file() or not fnmatch(entry.name, pattern):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                removed.append(str(entry))
                if not dry_run:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
        return removed

    @staticmethod
    def prune_empty_dirs(path: str) -> list[str]:
        """Remove empty subdirectories under *path* (breadth-first).

        Only removes directories that are completely empty (no files, no
        sub-dirs).  Safe: never removes *path* itself.

        Returns
        -------
        list[str]
            Paths of directories that were removed.
        """
        p = Path(path)
        removed: list[str] = []

        if not p.is_dir():
            return removed

        for entry in p.iterdir():
            if not entry.is_dir():
                continue
            try:
                if not any(entry.iterdir()):
                    entry.rmdir()
                    removed.append(str(entry))
            except OSError:
                pass

        return removed

    @staticmethod
    def clear_cache(*, errors: bool = True, messages: bool = True) -> dict[str, int]:
        """Remove all cached log files.

        Parameters
        ----------
        errors : bool
            Clear the ``errors/`` directory.
        messages : bool
            Clear the ``messages/`` directory.

        Returns
        -------
        dict[str, int]
            Count of files removed per directory role.
        """
        counts: dict[str, int] = {}
        targets: list[tuple[str, str]] = []
        if errors:
            targets.append(("errors", CachePaths.errors()))
        if messages:
            targets.append(("messages", CachePaths.messages()))
        for role, path in targets:
            counts[role] = _rmdir_contents(path)
        return counts

    @staticmethod
    def debug_info() -> dict[str, str]:
        """Return a snapshot of all computed paths (for diagnostics).

        Safe to call in any environment — does no I/O beyond
        ``os.getcwd()``.
        """
        return {
            "base_logs": CachePaths.base_logs(),
            "errors": CachePaths.errors(),
            "messages": CachePaths.messages(),
            "base_config": CachePaths.base_config(),
            "base_data": CachePaths.base_data(),
            "base_temp": CachePaths.base_temp(),
            "project_dir": _project_dir(),
            "cache_base": _get_cache_base(),
            "platform": sys.platform,
        }


# =============================================================================
# Internal helpers
# =============================================================================


def _mkdir_p(path: str, *, mode: int = 0o700) -> bool:
    """Create directory *path* if it does not exist.

    Returns ``True`` if the directory was created; ``False`` if it already
    existed or creation failed (permissions, etc.).
    """
    try:
        p = Path(path)
        if not p.exists():
            p.mkdir(parents=True, mode=mode, exist_ok=True)
            return True
        return False
    except OSError:
        return False


def _rmdir_contents(path: str) -> int:
    """Remove every file and subdirectory under *path*, then *path* itself.

    Returns the count of top-level entries removed.  Does not raise on
    failures (best-effort).
    """
    p = Path(path)
    if not p.is_dir():
        return 0

    count = 0
    for entry in list(p.iterdir()):
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
            count += 1
        except OSError:
            pass

    # Remove the top-level directory itself (will fail if not empty — safe)
    try:
        p.rmdir()
    except OSError:
        pass

    return count


# =============================================================================
# Module-level convenience alias
# =============================================================================

CACHE_PATHS = CachePaths
