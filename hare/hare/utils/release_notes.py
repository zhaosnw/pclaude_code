"""
Changelog fetch + release-notes UI helpers. Port of src/utils/releaseNotes.ts.
"""

from __future__ import annotations

import os
import re
from typing import Any

from hare.utils.config_full import get_global_config, save_global_config
from hare.utils.env_utils import get_hare_config_home_dir
from hare.utils.log import log_error
from hare.utils.privacy_level import is_essential_traffic_only
from hare.utils.semver import gt

try:
    from hare.bootstrap.state import get_is_non_interactive_session
except ImportError:

    def get_is_non_interactive_session() -> bool:
        return False


VERSION = "2.1.88"
MAX_RELEASE_NOTES_SHOWN = 5
CHANGELOG_URL = "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md"
RAW_CHANGELOG_URL = "https://raw.githubusercontent.com/anthropics/hare-code/refs/heads/main/CHANGELOG.md"

_changelog_memory_cache: str | None = None


def _reset_changelog_cache_for_testing() -> None:
    global _changelog_memory_cache
    _changelog_memory_cache = None


def _changelog_cache_path() -> str:
    return os.path.join(get_hare_config_home_dir(), "cache", "changelog.md")


async def migrate_changelog_from_config() -> None:
    config = get_global_config()
    cached = config.get("cachedChangelog") or config.get("cached_changelog")
    if not cached:
        return
    path = _changelog_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(str(cached))
    except FileExistsError:
        pass
    config.pop("cachedChangelog", None)
    config.pop("cached_changelog", None)
    save_global_config(config)


async def fetch_and_store_changelog() -> None:
    if get_is_non_interactive_session():
        return
    if is_essential_traffic_only():
        return
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(RAW_CHANGELOG_URL, timeout=30.0)
    except ImportError:
        return
    if r.status_code != 200:
        return
    content = r.text
    global _changelog_memory_cache
    if content == _changelog_memory_cache:
        return
    path = _changelog_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    _changelog_memory_cache = content
    cfg = get_global_config()
    cfg["changelogLastFetched"] = __import__("time").time() * 1000
    save_global_config(cfg)


async def get_stored_changelog() -> str:
    global _changelog_memory_cache
    if _changelog_memory_cache is not None:
        return _changelog_memory_cache
    path = _changelog_cache_path()
    try:
        with open(path, encoding="utf-8") as f:
            _changelog_memory_cache = f.read()
    except OSError:
        _changelog_memory_cache = ""
    return _changelog_memory_cache or ""


def get_stored_changelog_from_memory() -> str:
    return _changelog_memory_cache or ""


def parse_changelog(content: str) -> dict[str, list[str]]:
    try:
        if not content:
            return {}
        out: dict[str, list[str]] = {}
        sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
        for section in sections:
            lines = section.strip().split("\n")
            if not lines:
                continue
            version_line = lines[0]
            version = (version_line.split(" - ")[0] or "").strip()
            if not version:
                continue
            notes = [
                ln.strip()[2:].strip()
                for ln in lines[1:]
                if ln.strip().startswith("- ")
            ]
            notes = [n for n in notes if n]
            if notes:
                out[version] = notes
        return out
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return {}


def get_recent_release_notes(
    current_version: str,
    previous_version: str | None,
    changelog_content: str | None = None,
) -> list[str]:
    try:
        text = (
            changelog_content
            if changelog_content is not None
            else get_stored_changelog_from_memory()
        )
        release_notes = parse_changelog(text)
        prev = previous_version
        if not prev or gt(current_version, prev):
            flat: list[str] = []
            for ver, notes in sorted(
                release_notes.items(), key=lambda kv: kv[0], reverse=True
            ):
                if prev and not gt(ver, prev):
                    continue
                flat.extend(notes)
            return [n for n in flat if n][:MAX_RELEASE_NOTES_SHOWN]
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return []
    return []


def get_all_release_notes(
    changelog_content: str | None = None,
) -> list[tuple[str, list[str]]]:
    try:
        text = (
            changelog_content
            if changelog_content is not None
            else get_stored_changelog_from_memory()
        )
        release_notes = parse_changelog(text)
        versions = sorted(release_notes.keys(), key=lambda v: v)
        out: list[tuple[str, list[str]]] = []
        for v in versions:
            notes = [n for n in release_notes.get(v, []) if n]
            if notes:
                out.append((v, notes))
        return out
    except Exception as e:
        log_error(e if isinstance(e, Exception) else RuntimeError(str(e)))
        return []


async def check_for_release_notes(
    last_seen_version: str | None,
    current_version: str = VERSION,
) -> dict[str, Any]:
    if os.environ.get("USER_TYPE") == "ant":
        vc = os.environ.get("VERSION_CHANGELOG", "")
        commits = [c for c in vc.strip().split("\n") if c]
        return {"hasReleaseNotes": bool(commits), "releaseNotes": commits}
    cached = await get_stored_changelog()
    if last_seen_version != current_version or not cached:
        import asyncio

        asyncio.create_task(fetch_and_store_changelog())
    notes = get_recent_release_notes(current_version, last_seen_version, cached)
    return {"hasReleaseNotes": bool(notes), "releaseNotes": notes}


def check_for_release_notes_sync(
    last_seen_version: str | None,
    current_version: str = VERSION,
) -> dict[str, Any]:
    if os.environ.get("USER_TYPE") == "ant":
        vc = os.environ.get("VERSION_CHANGELOG", "")
        commits = [c for c in vc.strip().split("\n") if c]
        return {"hasReleaseNotes": bool(commits), "releaseNotes": commits}
    notes = get_recent_release_notes(current_version, last_seen_version)
    return {"hasReleaseNotes": bool(notes), "releaseNotes": notes}
