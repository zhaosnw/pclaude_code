"""
Centralized plugin directory configuration.

Port of: src/utils/plugins/pluginDirectories.ts
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from hare.bootstrap.state import get_use_cowork_plugins
from hare.utils.debug import log_for_debugging
from hare.utils.env_utils import get_hare_config_home_dir, is_env_truthy
from hare.utils.errors import error_message
from hare.utils.format import format_bytes
from hare.utils.permissions.path_validation import expand_tilde

PLUGINS_DIR = "plugins"
COWORK_PLUGINS_DIR = "cowork_plugins"


def _get_plugins_directory_name() -> str:
    if get_use_cowork_plugins():
        return COWORK_PLUGINS_DIR
    if is_env_truthy(os.environ.get("CLAUDE_CODE_USE_COWORK_PLUGINS")):
        return COWORK_PLUGINS_DIR
    return PLUGINS_DIR


def get_plugins_directory() -> str:
    env_override = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR")
    if env_override:
        return expand_tilde(env_override)
    return str(Path(get_hare_config_home_dir()) / _get_plugins_directory_name())


def get_plugin_seed_dirs() -> list[str]:
    raw = os.environ.get("CLAUDE_CODE_PLUGIN_SEED_DIR")
    if not raw:
        return []
    sep = os.pathsep
    return [expand_tilde(p) for p in raw.split(sep) if p]


def _sanitize_plugin_id(plugin_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\-_]", "-", plugin_id)


def plugin_data_dir_path(plugin_id: str) -> str:
    return str(Path(get_plugins_directory()) / "data" / _sanitize_plugin_id(plugin_id))


def get_plugin_data_dir(plugin_id: str) -> str:
    d = plugin_data_dir_path(plugin_id)
    Path(d).mkdir(parents=True, exist_ok=True)
    return d


def _is_fs_inaccessible(exc: BaseException) -> bool:
    return isinstance(exc, (PermissionError, OSError))


async def get_plugin_data_dir_size(
    plugin_id: str,
) -> dict[str, Any] | None:
    dir_path = plugin_data_dir_path(plugin_id)
    bytes_total = 0

    async def walk(p: str) -> None:
        nonlocal bytes_total
        try:
            entries = await asyncio.to_thread(lambda: list(os.scandir(p)))
        except OSError as e:
            if _is_fs_inaccessible(e):
                raise
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                await walk(entry.path)
            else:
                try:
                    st = await asyncio.to_thread(entry.stat)
                    bytes_total += st.st_size
                except OSError:
                    pass

    try:
        await walk(dir_path)
    except OSError as e:
        if _is_fs_inaccessible(e):
            return None
        raise
    if bytes_total == 0:
        return None
    return {"bytes": bytes_total, "human": format_bytes(bytes_total)}


async def delete_plugin_data_dir(plugin_id: str) -> None:
    d = plugin_data_dir_path(plugin_id)
    try:
        await asyncio.to_thread(shutil.rmtree, d, ignore_errors=True)
    except Exception as e:
        log_for_debugging(
            f"Failed to delete plugin data dir {d}: {error_message(e)}",
            level="warn",
        )
