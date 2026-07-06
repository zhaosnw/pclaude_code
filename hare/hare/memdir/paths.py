"""Auto-memory path resolution (port of src/memdir/paths.ts).

Security: projectSettings is excluded from autoMemoryDirectory override
to prevent malicious repos from redirecting memory writes to sensitive dirs.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path


def _env_truthy(val: str | None) -> bool:
    if not val:
        return False
    return val.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Memory enablement (TS paths.ts L30-55)
# ---------------------------------------------------------------------------


def is_auto_memory_enabled() -> bool:
    """Check if automatic memory is enabled.

    Priority chain (TS isAutoMemoryEnabled):
    1. CLAUDE_CODE_DISABLE_AUTO_MEMORY env (1/true → disabled)
    2. SIMPLE mode → disabled
    3. CCR without persistent storage → disabled
    4. settings.json autoMemoryEnabled field
    5. Default: enabled
    """
    if _env_truthy(os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")):
        return False
    if _env_truthy(os.environ.get("CLAUDE_CODE_SIMPLE")):
        return False
    if _env_truthy(os.environ.get("CLAUDE_CODE_REMOTE")) and not os.environ.get(
        "CLAUDE_CODE_REMOTE_MEMORY_DIR"
    ):
        return False
    # Check settings.json autoMemoryEnabled (user/local/flag/policy only;
    # projectSettings excluded for security — TS paths.ts L179-186)
    try:
        from hare.utils.settings.settings import (
            _read_setting_excluding_project,
            TRUSTED_SOURCES_EXCLUDING_PROJECT,
        )

        val = _read_setting_excluding_project(
            "autoMemoryEnabled", TRUSTED_SOURCES_EXCLUDING_PROJECT
        )
        if val is not None:
            return bool(val)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Path resolution (TS paths.ts L89-278)
# ---------------------------------------------------------------------------


def get_memory_base_dir() -> str:
    """Base directory for auto-memory storage."""
    return os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR") or str(
        Path.home() / ".claude"
    )


@lru_cache(maxsize=1)
def get_auto_mem_path() -> str:
    """Resolved auto-memory directory with trailing separator.

    Path resolution priority (TS paths.ts):
    1. CLAUDE_COWORK_MEMORY_PATH_OVERRIDE env (full path override)
    2. autoMemoryDirectory setting (policySettings > localSettings > userSettings;
       projectSettings excluded for security)
    3. Default: <base>/projects/<project_key>/memory/
    """
    # 1. Cowork override
    override = os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
    if override:
        p = Path(override)
        return str(p if str(p).endswith(os.sep) else p) + os.sep

    # 2. Settings.json override (trusted sources only, TS paths.ts L179-186)
    base = get_memory_base_dir()
    try:
        from hare.utils.settings.settings import (
            _read_setting_excluding_project,
            TRUSTED_SOURCES_EXCLUDING_PROJECT,
        )

        setting_override = _read_setting_excluding_project(
            "autoMemoryDirectory", TRUSTED_SOURCES_EXCLUDING_PROJECT
        )
        if setting_override and isinstance(setting_override, str):
            validated = _validate_memory_path(setting_override, base)
            if validated:
                return validated
    except Exception:
        pass

    # 3. Default: ~/.claude/projects/<project_key>/memory/
    project_key = _resolve_project_key()
    return str(Path(base) / "projects" / project_key / "memory") + os.sep


def _resolve_project_key() -> str:
    """Resolve project key for memory directory segregation.

    TS: sanitizePath(canonicalGitRoot) — strips special chars, length-capped.
    Falls back to env var or 'default'.
    """
    key = os.environ.get("CLAUDE_PROJECT_KEY")
    if key:
        return _sanitize_project_key(key)
    # Try git root the
    try:
        cwd = os.getcwd()
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if result.returncode == 0:
            git_root = result.stdout.strip()
            return _sanitize_project_key(git_root)
    except Exception:
        pass
    return "default"


def _sanitize_project_key(raw: str) -> str:
    """Sanitize a path to a safe project key (alphanumeric + dashes/underscores).

    TS: sanitizePath — strips non-alphanumeric chars, length-caps at 64.
    """
    # Replace path separators with dashes
    cleaned = raw.replace("\\", "/").rstrip("/")
    # Keep only alphanumeric, dash, underscore, dot
    safe = re.sub(r"[^a-zA-Z0-9._-]", "-", cleaned)
    # Collapse consecutive dashes
    safe = re.sub(r"-{2,}", "-", safe)
    # Strip leading/trailing dashes
    safe = safe.strip("-")
    # Cap at 64 chars
    if len(safe) > 64:
        safe = safe[:64]
    return safe or "default"


# ---------------------------------------------------------------------------
# Security path validation (TS paths.ts L109-150)
# ---------------------------------------------------------------------------


def _validate_memory_path(candidate: str, base_dir: str) -> str | None:
    """Validate a user-supplied memory directory path.

    TS: validateMemoryPath — rejects:
    - Relative paths (../../etc)
    - Root or near-root paths (/, /tmp, C:\)
    - Windows drive-root paths
    - UNC paths (\\server\share)
    - Null byte injection
    - URL-encoded traversal (%2e%2e%2f)
    - Unicode normalization attacks (fullwidth chars)
    """

    # Null byte injection
    if "\0" in candidate:
        return None

    # URL-encoded traversal
    decoded = _decode_url_safe(candidate)
    if decoded != candidate:
        return None

    # Unicode normalization attack
    if _contains_fullwidth_chars(candidate):
        return None

    # Expand user
    path = os.path.expanduser(candidate)

    # Must be absolute
    if not os.path.isabs(path):
        return None

    # Reject root paths
    normalized = os.path.normpath(path)
    parent = os.path.dirname(normalized)
    if not parent or parent == normalized:
        return None  # root path

    # Check depth — must have at least 3 components deeper than /
    parts = [p for p in normalized.split(os.sep) if p]
    if len(parts) < 3:
        return None

    # Reject UNC paths
    if path.startswith("\\\\") or path.startswith("//"):
        return None

    # Resolve symlinks and verify containment
    try:
        real = os.path.realpath(path)
    except OSError:
        return None

    base_real = os.path.realpath(base_dir)
    if not real.startswith(base_real + os.sep) and real != base_real:
        return None

    return real + os.sep if not real.endswith(os.sep) else real


def _decode_url_safe(s: str) -> str:
    """Attempt URL decode; return original if it changes (indicates attempted injection)."""
    from urllib.parse import unquote

    try:
        decoded = unquote(s)
        return decoded if decoded != s else s
    except Exception:
        return s


def _contains_fullwidth_chars(s: str) -> bool:
    """Check for Unicode fullwidth characters used in path traversal attacks.

    TS: NFKC normalization check — fullwidth period U+FF0E, solidus U+FF0F.
    """
    for ch in s:
        cp = ord(ch)
        # Fullwidth ASCII range: U+FF01 to U+FF5E
        if 0xFF01 <= cp <= 0xFF5E:
            return True
        # Specific fullwidth: period U+FF0E, solidus U+FF0F
        if cp in (0xFF0E, 0xFF0F):
            return True
    return False


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------


def get_auto_mem_entrypoint() -> str:
    return str(Path(get_auto_mem_path()) / "MEMORY.md")


def get_auto_mem_daily_log_path(date: object | None = None) -> str:
    import datetime

    d = date or datetime.datetime.now()
    if not isinstance(d, datetime.datetime):
        d = datetime.datetime.now()
    y, m, day = d.year, d.month, d.day
    return str(
        Path(get_auto_mem_path())
        / "logs"
        / str(y)
        / f"{m:02d}"
        / f"{y}-{m:02d}-{day:02d}.md"
    )


def is_auto_mem_path(absolute_path: str) -> bool:
    mem = os.path.realpath(get_auto_mem_path().rstrip(os.sep))
    target = os.path.realpath(os.path.normpath(absolute_path))
    return target.startswith(mem + os.sep) or target == mem


def has_auto_mem_path_override() -> bool:
    return bool(os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"))


def get_max_entrypoint_lines() -> int:
    return 200


def get_max_entrypoint_bytes() -> int:
    return 25_000


def truncate_entrypoint_content(
    content: str,
    max_lines: int = 200,
    max_bytes: int = 25_000,
) -> dict[str, object]:
    """Truncate MEMORY.md content to capacity limits.

    TS: truncateEntrypointContent — line-truncates first, then byte-truncates
    at the last newline before max_bytes. Appends warning if truncated.

    Returns: dict with content, line_count, byte_count, was_line_truncated, was_byte_truncated.
    """
    lines = content.split("\n")
    line_count = len(lines)
    was_line_truncated = False
    was_byte_truncated = False

    # Line truncation
    if line_count > max_lines:
        lines = lines[:max_lines]
        was_line_truncated = True

    # Byte truncation: find last newline before max_bytes
    rejoined = "\n".join(lines)
    byte_count = len(rejoined.encode("utf-8"))
    if byte_count > max_bytes:
        # Walk backward through lines until we fit
        while len(lines) > 0:
            lines = lines[:-1]
            rejoined = "\n".join(lines)
            if len(rejoined.encode("utf-8")) <= max_bytes:
                break
        byte_count = len(rejoined.encode("utf-8"))
        was_byte_truncated = True

    # Append warning if truncated
    if was_line_truncated or was_byte_truncated:
        limit_desc = []
        if was_line_truncated:
            limit_desc.append(f"{max_lines} lines")
        if was_byte_truncated:
            limit_desc.append(f"{max_bytes // 1000}KB")
        warning = (
            f"\n\n[MEMORY.md truncated — exceeded {' and '.join(limit_desc)} cap. "
            f"Some memory entries are not listed. Run `/memory` to see all entries.]"
        )
        lines.append(warning)
        rejoined = "\n".join(lines)
        byte_count = len(rejoined.encode("utf-8"))

    return {
        "content": rejoined,
        "line_count": line_count,
        "byte_count": byte_count,
        "was_line_truncated": was_line_truncated,
        "was_byte_truncated": was_byte_truncated,
    }


def is_extract_mode_active() -> bool:
    """Check if memory extraction is active for this session.

    TS paths.ts L69-77: gates on tengu_passport_quail GrowthBook feature
    + non-interactive session check.
    """
    if not is_auto_memory_enabled():
        return False
    # TS: also checks getIsNonInteractiveSession()
    try:
        from hare.bootstrap.state import get_is_non_interactive_session

        if get_is_non_interactive_session():
            return False
    except ImportError:
        pass
    return True
