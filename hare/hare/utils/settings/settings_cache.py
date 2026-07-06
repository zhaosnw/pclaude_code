"""Settings cache for session-level and per-source caching.

Port of: src/utils/settings/settingsCache.ts

Provides:
- sessionSettingsCache: merged result from loadSettingsFromDisk()
- perSourceCache: per-source cache for getSettingsForSource() with proper
  cache-miss semantics (distinguishes "not yet loaded" from "loaded = null")
- parseFileCache: path-keyed cache for parseSettingsFile() to dedupe
  disk reads + validation during startup when multiple callers read the
  same file paths
- pluginSettingsBase: lowest-priority base layer from plugins
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Types (matching TS validation.ts + settingsCache.ts)
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    """Validation error surfaced during settings parsing / schema check.

    TS: type ValidationError in validation.ts
    """

    file: str | None = None
    path: str = ""
    message: str = ""
    expected: str | None = None
    invalid_value: Any = None
    suggestion: str | None = None
    doc_link: str | None = None
    # MCP-specific metadata (only present for MCP configuration errors)
    mcp_error_metadata: dict[str, Any] | None = None


@dataclass
class ParsedSettings:
    """Result of parsing a single settings file.

    TS: type ParsedSettings in settingsCache.ts
    """

    settings: dict[str, Any] | None = None
    errors: list[ValidationError] = field(default_factory=list)


@dataclass
class SettingsWithErrors:
    """Merged settings across all sources, plus all validation errors encountered.

    TS: type SettingsWithErrors in validation.ts
    """

    settings: dict[str, Any] = field(default_factory=dict)
    errors: list[ValidationError] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when no settings were loaded and no errors were found."""
        return not self.settings and not self.errors


# ---------------------------------------------------------------------------
# Cache sentinel for per-source cache-miss detection
# ---------------------------------------------------------------------------

# The per-source cache needs to distinguish three states:
#   (a) "key not in map"        → cache miss, caller should compute
#   (b) "key in map, value None" → cached "no settings for this source"
#   (c) "key in map, value dict" → cached settings
#
# TS uses Map<SettingSource, SettingsJson | null> and Map.has() for this.
# Python dict.get() returns None for both (a) and (b), so we use a sentinel.

_UNDEFINED: Any = object()  # unique sentinel object — NOT None


# ---------------------------------------------------------------------------
# Session-level merged settings cache (TS: sessionSettingsCache)
# ---------------------------------------------------------------------------

_session_settings_cache: SettingsWithErrors | None = None


def get_session_settings_cache() -> SettingsWithErrors | None:
    """Get the cached merged settings with errors, or None if not yet loaded.

    TS: getSessionSettingsCache()
    """
    return _session_settings_cache


def set_session_settings_cache(value: SettingsWithErrors) -> None:
    """Set the cached merged settings with errors.

    TS: setSessionSettingsCache(value)
    """
    global _session_settings_cache
    _session_settings_cache = value


# Legacy aliases for callers using the old naming convention
def get_session_settings() -> SettingsWithErrors | None:
    """Legacy alias for get_session_settings_cache()."""
    return _session_settings_cache


def set_session_settings(settings: SettingsWithErrors) -> None:
    """Legacy alias for set_session_settings_cache()."""
    global _session_settings_cache
    _session_settings_cache = settings


# ---------------------------------------------------------------------------
# Per-source settings cache (TS: perSourceCache)
# ---------------------------------------------------------------------------

_per_source_cache: dict[str, dict[str, Any] | None] = {}


def get_cached_settings_for_source(source: str) -> dict[str, Any] | None:
    """Get cached settings for a single source.

    Returns None on cache miss (distinct from cached-None which means
    "this source was loaded and has no settings").

    TS: getCachedSettingsForSource(source)
    """
    if source not in _per_source_cache:
        return None  # cache miss
    return _per_source_cache[source]


def set_cached_settings_for_source(
    source: str, value: dict[str, Any] | None
) -> None:
    """Set cached settings for a single source.

    TS: setCachedSettingsForSource(source, value)
    """
    _per_source_cache[source] = value


# Legacy aliases
def get_per_source_cache(source: str) -> dict[str, Any] | None:
    """Legacy alias for get_cached_settings_for_source()."""
    return get_cached_settings_for_source(source)


def set_per_source_cache(source: str, settings: dict[str, Any] | None) -> None:
    """Legacy alias for set_cached_settings_for_source()."""
    set_cached_settings_for_source(source, settings)


# ---------------------------------------------------------------------------
# Parse-file cache (TS: parseFileCache)
# ---------------------------------------------------------------------------

# Path-keyed cache for parseSettingsFile results. Both getSettingsForSource
# and loadSettingsFromDisk call parseSettingsFile on the same paths during
# startup — this dedupes the disk read + validation.
#
# TS: const parseFileCache = new Map<string, ParsedSettings>()

_parse_file_cache: dict[str, ParsedSettings] = {}


def get_cached_parsed_file(path: str) -> ParsedSettings | None:
    """Get a cached parsed-file result by absolute path.

    Returns None on cache miss (path has not been parsed yet).

    TS: getCachedParsedFile(path)
    """
    return _parse_file_cache.get(path)


def set_cached_parsed_file(path: str, value: ParsedSettings) -> None:
    """Cache a parsed-file result by absolute path.

    TS: setCachedParsedFile(path, value)
    """
    _parse_file_cache[path] = value


def parse_file_cache_has(path: str) -> bool:
    """Check whether the parse-file cache contains an entry for `path`.

    TS: parseFileCache.has(path)  (used internally for cache-miss checks)
    """
    return path in _parse_file_cache


# ---------------------------------------------------------------------------
# Plugin settings base layer (TS: pluginSettingsBase)
# ---------------------------------------------------------------------------

# Plugin settings base layer for the settings cascade.
# pluginLoader writes here after loading plugins;
# loadSettingsFromDisk reads it as the lowest-priority base.
_plugin_settings_base: dict[str, Any] | None = None


def get_plugin_settings_base() -> dict[str, Any] | None:
    """Get the plugin settings base layer for the settings cascade.

    TS: getPluginSettingsBase()
    """
    return _plugin_settings_base


def set_plugin_settings_base(settings: dict[str, Any] | None) -> None:
    """Set the plugin settings base layer.

    TS: setPluginSettingsBase(settings)
    """
    global _plugin_settings_base
    _plugin_settings_base = settings


def clear_plugin_settings_base() -> None:
    """Clear the plugin settings base layer to undefined.

    TS: clearPluginSettingsBase()
    """
    global _plugin_settings_base
    _plugin_settings_base = None


# ---------------------------------------------------------------------------
# Cache lifecycle (TS: resetSettingsCache / clearSettingsCaches)
# ---------------------------------------------------------------------------


def reset_settings_cache() -> None:
    """Reset all settings caches (TS parity alias for clear_caches).

    Invalidates the session cache, per-source cache, and parse-file cache.
    Called on settings write, --add-dir, plugin init, hooks refresh.

    TS: resetSettingsCache()
    """
    clear_caches()


def clear_caches() -> None:
    """Clear all settings caches.

    Matching TS clearSettingsCaches:
    - sessionSettingsCache = null
    - perSourceCache.clear()
    - parseFileCache.clear()
    """
    global _session_settings_cache
    _session_settings_cache = None
    _per_source_cache.clear()
    _parse_file_cache.clear()


def clear_parse_file_cache() -> None:
    """Clear only the parse-file cache (leaves session + per-source intact).

    Useful when a single file has been updated and only its parse result
    needs to be invalidated without blowing away the merged session cache.
    """
    _parse_file_cache.clear()


def invalidate_parse_file_entry(path: str) -> None:
    """Remove a single path from the parse-file cache.

    Call after writing or deleting a settings file so the next read
    goes to disk instead of returning the stale cached entry.
    """
    _parse_file_cache.pop(path, None)


def get_cache_stats() -> dict[str, Any]:
    """Return diagnostic counts for the settings cache layers.

    Useful for debugging and /status-style introspection.
    """
    return {
        "session_loaded": _session_settings_cache is not None,
        "per_source_entries": len(_per_source_cache),
        "parse_file_entries": len(_parse_file_cache),
        "plugin_base_loaded": _plugin_settings_base is not None,
    }
