"""
Plugin version calculation for cache paths and update detection.

Port of: src/utils/plugins/pluginVersioning.ts
"""

from __future__ import annotations

import hashlib
from typing import Any

from hare.utils.debug import log_for_debugging
from hare.utils.git.git_filesystem import get_head_for_dir


async def calculate_plugin_version(
    plugin_id: str,
    source: Any,
    manifest: dict[str, Any] | None = None,
    install_path: str | None = None,
    provided_version: str | None = None,
    git_commit_sha: str | None = None,
) -> str:
    if manifest and manifest.get("version"):
        log_for_debugging(
            f"Using manifest version for {plugin_id}: {manifest['version']}"
        )
        return str(manifest["version"])

    if provided_version:
        log_for_debugging(f"Using provided version for {plugin_id}: {provided_version}")
        return provided_version

    if git_commit_sha:
        short_sha = git_commit_sha[:12]
        if isinstance(source, dict) and source.get("source") == "git-subdir":
            path = str(source.get("path") or "")
            norm = (
                path.replace("\\", "/").replace("./", "", 1)
                if path.startswith("./")
                else path.replace("\\", "/")
            )
            norm = norm.rstrip("/")
            path_hash = hashlib.sha256(norm.encode()).hexdigest()[:8]
            v = f"{short_sha}-{path_hash}"
            log_for_debugging(
                f"Using git-subdir SHA+path version for {plugin_id}: {v} (path={norm})"
            )
            return v
        log_for_debugging(f"Using pre-resolved git SHA for {plugin_id}: {short_sha}")
        return short_sha

    if install_path:
        sha = await get_git_commit_sha(install_path)
        if sha:
            short_sha = sha[:12]
            log_for_debugging(f"Using git SHA for {plugin_id}: {short_sha}")
            return short_sha

    log_for_debugging(f"No version found for {plugin_id}, using 'unknown'")
    return "unknown"


async def get_git_commit_sha(dir_path: str) -> str | None:
    return await get_head_for_dir(dir_path)


def get_version_from_path(install_path: str) -> str | None:
    parts = [p for p in install_path.replace("\\", "/").split("/") if p]
    try:
        cache_index = next(
            i
            for i, part in enumerate(parts)
            if part == "cache" and i > 0 and parts[i - 1] == "plugins"
        )
    except StopIteration:
        return None
    after = parts[cache_index + 1 :]
    if len(after) >= 3:
        return after[2] or None
    return None


def is_versioned_path(path: str) -> bool:
    return get_version_from_path(path) is not None
