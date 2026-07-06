"""
Path expansion and normalization. Port of src/utils/path.ts (expandPath, etc.).

Note: `hare.utils.path` contains a slimmer port; this module mirrors the full TS API.
"""

from __future__ import annotations

import os
import re
import unicodedata

from hare.utils.cwd import get_cwd
from hare.utils.platform import get_platform
from hare.utils.windows_paths import posix_path_to_windows_path


def expand_path(path: str, base_dir: str | None = None) -> str:
    """Expand ~, resolve relative paths, normalize NFC; POSIX /c/... on Windows."""
    actual_base = base_dir if base_dir is not None else (get_cwd() or os.getcwd())
    if not isinstance(path, str):
        raise TypeError(f"Path must be a string, received {type(path).__name__}")
    if not isinstance(actual_base, str):
        raise TypeError(
            f"Base directory must be a string, received {type(actual_base).__name__}"
        )
    if "\0" in path or "\0" in actual_base:
        raise ValueError("Path contains null bytes")
    trimmed = path.strip()
    if not trimmed:
        return unicodedata.normalize("NFC", os.path.normpath(actual_base))
    if trimmed == "~":
        return unicodedata.normalize("NFC", os.path.expanduser("~"))
    if trimmed.startswith("~/"):
        joined = os.path.join(os.path.expanduser("~"), trimmed[2:])
        return unicodedata.normalize("NFC", os.path.normpath(joined))
    processed = trimmed
    if get_platform() == "windows" and re.match(r"^/[a-z]/", trimmed, re.I):
        try:
            processed = posix_path_to_windows_path(trimmed)
        except Exception:
            processed = trimmed
    if os.path.isabs(processed):
        return unicodedata.normalize("NFC", os.path.normpath(processed))
    return unicodedata.normalize(
        "NFC", os.path.normpath(os.path.join(actual_base, processed))
    )


def to_relative_path(absolute_path: str) -> str:
    cwd = get_cwd()
    try:
        rel = os.path.relpath(absolute_path, cwd)
    except ValueError:
        return absolute_path
    return absolute_path if rel.startswith("..") else rel


def get_directory_for_path(path: str) -> str:
    absolute_path = expand_path(path)
    if absolute_path.startswith("\\\\") or absolute_path.startswith("//"):
        return os.path.dirname(absolute_path)
    try:
        if os.path.isdir(absolute_path):
            return absolute_path
    except OSError:
        pass
    return os.path.dirname(absolute_path)


def contains_path_traversal(path: str) -> bool:
    return bool(re.search(r"(?:^|[\\/])\.\.(?:[\\/]|$)", path))


def sanitize_path(path: str) -> str:
    """Normalize for safe storage keys (port stub; see sessionStoragePortable)."""
    return path.replace("\\", "/")


def normalize_path_for_config_key(path: str) -> str:
    normalized = os.path.normpath(path)
    return normalized.replace("\\", "/")
