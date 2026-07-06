"""JetBrains Hare plugin discovery — port of `jetbrains.ts`."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from hare.utils.fs_operations import get_fs_implementation

PLUGIN_PREFIX = "hare-code-jetbrains-plugin"

IDE_NAME_TO_DIR: dict[str, list[str]] = {
    "pycharm": ["PyCharm"],
    "intellij": ["IntelliJIdea", "IdeaIC"],
    "webstorm": ["WebStorm"],
    "phpstorm": ["PhpStorm"],
    "rubymine": ["RubyMine"],
    "clion": ["CLion"],
    "goland": ["GoLand"],
    "rider": ["Rider"],
    "datagrip": ["DataGrip"],
    "appcode": ["AppCode"],
    "dataspell": ["DataSpell"],
    "aqua": ["Aqua"],
    "gateway": ["Gateway"],
    "fleet": ["Fleet"],
    "androidstudio": ["AndroidStudio"],
}


def _build_common_plugin_directory_paths(ide_name: str) -> list[str]:
    home = Path.home()
    directories: list[str] = []
    patterns = IDE_NAME_TO_DIR.get(ide_name.lower())
    if not patterns:
        return directories

    app_data = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
    local_app_data = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")

    if sys.platform == "darwin":
        directories.extend(
            [
                str(home / "Library" / "Application Support" / "JetBrains"),
                str(home / "Library" / "Application Support"),
            ]
        )
        if ide_name.lower() == "androidstudio":
            directories.append(str(home / "Library" / "Application Support" / "Google"))
    elif sys.platform == "win32":
        directories.extend(
            [
                os.path.join(app_data, "JetBrains"),
                os.path.join(local_app_data, "JetBrains"),
                app_data,
            ]
        )
        if ide_name.lower() == "androidstudio":
            directories.append(os.path.join(local_app_data, "Google"))
    else:
        directories.extend(
            [
                str(home / ".config" / "JetBrains"),
                str(home / ".local" / "share" / "JetBrains"),
            ]
        )
        for pat in patterns:
            directories.append(str(home / f".{pat}"))
        if ide_name.lower() == "androidstudio":
            directories.append(str(home / ".config" / "Google"))

    return directories


async def _detect_plugin_directories(ide_name: str) -> list[str]:
    found: list[str] = []
    ide_patterns = IDE_NAME_TO_DIR.get(ide_name.lower())
    if not ide_patterns:
        return found
    regexes = [re.compile("^" + p) for p in ide_patterns]

    for base_dir in _build_common_plugin_directory_paths(ide_name):
        try:
            with os.scandir(base_dir) as it:
                entries = list(it)
        except OSError:
            continue
        for rx in regexes:
            for entry in entries:
                if not rx.match(entry.name):
                    continue
                if not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    continue
                dir_path = os.path.join(base_dir, entry.name)
                if sys.platform.startswith("linux"):
                    found.append(dir_path)
                    continue
                plugin_dir = os.path.join(dir_path, "plugins")
                if os.path.isdir(plugin_dir):
                    found.append(plugin_dir)

    seen: set[str] = set()
    out: list[str] = []
    for d in found:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


async def is_jetbrains_plugin_installed(ide_type: str) -> bool:
    dirs = await _detect_plugin_directories(ide_type)
    fs = get_fs_implementation()
    for d in dirs:
        plugin_path = os.path.join(d, PLUGIN_PREFIX)
        try:
            fs.stat_sync(plugin_path)
            return True
        except OSError:
            continue
    return False


_plugin_installed_cache: dict[str, bool] = {}
_plugin_lock: dict[str, asyncio.Lock] = {}


def _lock_for(ide: str) -> asyncio.Lock:
    if ide not in _plugin_lock:
        _plugin_lock[ide] = asyncio.Lock()
    return _plugin_lock[ide]


async def is_jetbrains_plugin_installed_cached(
    ide_type: str, force_refresh: bool = False
) -> bool:
    if force_refresh:
        _plugin_installed_cache.pop(ide_type, None)
    async with _lock_for(ide_type):
        if ide_type in _plugin_installed_cache and not force_refresh:
            return _plugin_installed_cache[ide_type]
        r = await is_jetbrains_plugin_installed(ide_type)
        _plugin_installed_cache[ide_type] = r
        return r


def is_jetbrains_plugin_installed_cached_sync(ide_type: str) -> bool:
    return _plugin_installed_cache.get(ide_type, False)
