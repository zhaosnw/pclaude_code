"""
Harbor channel plugin allowlist (GrowthBook-backed).

Port of: src/services/mcp/channelAllowlist.ts

Manages the allowlist of channel plugins that are permitted to operate
within the Harbor system. Supports:
- Wildcard pattern matching for plugins and marketplaces (fnmatch globs)
- Caching with configurable TTL for GrowthBook lookups
- Validation of allowlist entries (structure, types, required fields)
- Proper error handling, logging, and edge-case handling
- Programmatic additions for testing and dynamic registration
"""

from __future__ import annotations

import fnmatch
import logging
import time
from dataclasses import dataclass
from typing import Optional

from hare.services.analytics.growthbook import get_feature_value_cached_may_be_stale

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROWTHBOOK_FEATURE_KEY = "tengu_harbor_ledger"
CHANNELS_ENABLED_KEY = "tengu_harbor"
WILDCARD = "*"
ALLOWLIST_CACHE_TTL = 300  # 5 minutes — reduce load on GrowthBook while staying responsive

# ---------------------------------------------------------------------------
# Allowlist entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelAllowlistEntry:
    """A single entry in the channel allowlist.

    Each entry defines a permitted (plugin, marketplace) pair.
    Either field may be ``'*'`` to match any plugin or marketplace.
    """

    marketplace: str
    plugin: str


# ---------------------------------------------------------------------------
# Plugin identifier parsing
# ---------------------------------------------------------------------------


def _parse_plugin_identifier(plugin_source: str) -> tuple[str, str | None]:
    """Minimal ``plugin:name@marketplace`` parse for allowlist checks.

    Returns ``(name, marketplace)`` where marketplace may be ``None``
    if the format is not recognized.

    Supported formats::

        plugin:my-plugin@github
        plugin:*@github          (wildcard plugin name)
        plugin:my-plugin@*       (wildcard marketplace)
    """
    if "@" not in plugin_source or ":" not in plugin_source:
        return plugin_source, None
    try:
        left, marketplace = plugin_source.rsplit("@", 1)
        _, name = left.split(":", 1)
        return name, marketplace
    except ValueError:
        return plugin_source, None


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def _wildcard_match(text: str, pattern: str) -> bool:
    """Match a string against a glob pattern using shell-style wildcards.

    ``*`` matches everything, ``?`` matches a single character,
    ``[seq]`` matches any character in seq.
    """
    if pattern == WILDCARD:
        return True
    return fnmatch.fnmatch(text, pattern)


def _matches_channel(
    plugin: str, marketplace: str, entry: ChannelAllowlistEntry
) -> bool:
    """Check whether a (plugin, marketplace) pair matches an allowlist entry.

    Both the plugin and marketplace fields in the entry may contain
    wildcards (``*``).  A bare ``*`` matches any value, while patterns
    like ``slack-*`` match prefixes.
    """
    plugin_match = _wildcard_match(plugin, entry.plugin)
    marketplace_match = _wildcard_match(marketplace, entry.marketplace)
    return plugin_match and marketplace_match


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_valid_allowlist_entry(raw: object) -> tuple[bool, str]:
    """Validate the shape of a single raw allowlist entry.

    Returns ``(is_valid, error_message)``.  The error message is empty
    when the entry is valid.

    A valid entry must be a dict with string ``marketplace`` and
    ``plugin`` keys (both non-empty after stripping).
    """
    if not isinstance(raw, dict):
        return False, f"Expected dict, got {type(raw).__name__}"

    m = raw.get("marketplace")
    p = raw.get("plugin")

    if not isinstance(m, str):
        return False, f"'marketplace' must be str, got {type(m).__name__}"
    if not isinstance(p, str):
        return False, f"'plugin' must be str, got {type(p).__name__}"
    if not m.strip():
        return False, "'marketplace' is empty or whitespace-only"
    if not p.strip():
        return False, "'plugin' is empty or whitespace-only"

    return True, ""


def validate_allowlist(raw: object) -> tuple[list[ChannelAllowlistEntry], list[str]]:
    """Validate raw allowlist data from GrowthBook.

    Returns ``(valid_entries, warnings)`` — entries that fail validation
    are silently dropped but their warnings are returned so callers
    can log or expose them.
    """
    if not isinstance(raw, list):
        return [], ["Allowlist data is not a list"]

    entries: list[ChannelAllowlistEntry] = []
    warnings: list[str] = []

    for i, item in enumerate(raw):
        is_valid, error = _is_valid_allowlist_entry(item)
        if is_valid:
            assert isinstance(item, dict)  # validated above
            entries.append(
                ChannelAllowlistEntry(
                    marketplace=item["marketplace"].strip(),
                    plugin=item["plugin"].strip(),
                )
            )
        else:
            warnings.append(
                f"Allowlist entry #{i} invalid: {error} — "
                f"raw={item!r}"
            )

    return entries, warnings


# ---------------------------------------------------------------------------
# Allowlist cache
# ---------------------------------------------------------------------------

_allowlist_cache: Optional[tuple[list[ChannelAllowlistEntry], float]] = None


def _get_cached_allowlist() -> list[ChannelAllowlistEntry] | None:
    """Return the cached allowlist if still fresh, otherwise ``None``."""
    global _allowlist_cache
    if _allowlist_cache is None:
        return None
    entries, ts = _allowlist_cache
    if time.time() - ts < ALLOWLIST_CACHE_TTL:
        return entries
    _allowlist_cache = None
    return None


def _set_cached_allowlist(entries: list[ChannelAllowlistEntry]) -> None:
    """Store allowlist entries in the local cache."""
    global _allowlist_cache
    _allowlist_cache = (list(entries), time.time())


def clear_channel_allowlist_cache() -> None:
    """Clear the local allowlist cache so the next call re-fetches from GrowthBook."""
    global _allowlist_cache
    _allowlist_cache = None


def reset_channel_allowlist_for_testing() -> None:
    """Reset all allowlist state.  Equivalent to clearing the local cache."""
    clear_channel_allowlist_cache()


# ---------------------------------------------------------------------------
# Allowlist retrieval
# ---------------------------------------------------------------------------


def get_channel_allowlist() -> list[ChannelAllowlistEntry]:
    """Return the validated channel allowlist from GrowthBook.

    Results are cached locally for ``ALLOWLIST_CACHE_TTL`` seconds.
    Invalid or malformed entries are dropped with a warning log.
    """
    cached = _get_cached_allowlist()
    if cached is not None:
        return cached

    raw = get_feature_value_cached_may_be_stale(GROWTHBOOK_FEATURE_KEY, [])
    entries, warnings = validate_allowlist(raw)

    if warnings:
        for w in warnings:
            logger.warning("channel_allowlist: %s", w)

    _set_cached_allowlist(entries)
    return entries


def is_channels_enabled() -> bool:
    """Check whether the Harbor channel feature gate is enabled."""
    return bool(get_feature_value_cached_may_be_stale(CHANNELS_ENABLED_KEY, False))


# ---------------------------------------------------------------------------
# Allowlist checks
# ---------------------------------------------------------------------------


def _check_against_allowlist(plugin: str, marketplace: str) -> bool:
    """Core check: does a (plugin, marketplace) pair match any entry in the effective allowlist?"""
    effective = get_effective_allowlist()
    if not effective:
        return False
    return any(_matches_channel(plugin, marketplace, e) for e in effective)


def is_channel_allowlisted(plugin_source: str | None) -> bool:
    """Check whether a plugin source identifier is in the allowlist.

    ``plugin_source`` is expected in ``plugin:name@marketplace`` format.
    Returns ``False`` when:
    - ``plugin_source`` is ``None`` or empty
    - The format cannot be parsed (missing ``@`` or ``:``)
    - No matching entry is found in the allowlist
    - The channels feature gate is disabled
    """
    if not plugin_source:
        return False

    name, marketplace = _parse_plugin_identifier(plugin_source)
    if not marketplace:
        return False

    return _check_against_allowlist(name, marketplace)


def is_channel_allowlisted_explicit(
    plugin: str, marketplace: str
) -> bool:
    """Check whether an explicit (plugin, marketplace) pair is allowlisted.

    Unlike ``is_channel_allowlisted()`` this accepts already-parsed values,
    avoiding the need to re-format into ``plugin:name@marketplace`` syntax.
    """
    if not plugin or not marketplace:
        return False

    return _check_against_allowlist(plugin, marketplace)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_allowlisted_marketplaces_for_plugin(plugin: str) -> list[str]:
    """Return all marketplaces that a given plugin is allowlisted for.

    Wildcards in the allowlist are *not* expanded — only literal
    marketplace values are returned.  A ``*`` entry means the plugin
    is allowlisted for every marketplace; this is signalled by the
    special string ``"*"`` appearing in the result.
    """
    if not plugin:
        return []

    allowlist = get_channel_allowlist()
    result: list[str] = []

    for entry in allowlist:
        if _wildcard_match(plugin, entry.plugin):
            result.append(entry.marketplace)

    return result


def get_allowlisted_plugins_for_marketplace(marketplace: str) -> list[str]:
    """Return all plugins allowlisted for a given marketplace.

    Analogous to ``get_allowlisted_marketplaces_for_plugin()`` but
    queries in the opposite direction.
    """
    if not marketplace:
        return []

    allowlist = get_channel_allowlist()
    result: list[str] = []

    for entry in allowlist:
        if _wildcard_match(marketplace, entry.marketplace):
            result.append(entry.plugin)

    return result


def get_all_allowlisted_marketplaces() -> list[str]:
    """Return every marketplace that appears in the allowlist (deduplicated)."""
    return list({e.marketplace for e in get_channel_allowlist()})


def get_all_allowlisted_plugins() -> list[str]:
    """Return every plugin that appears in the allowlist (deduplicated)."""
    return list({e.plugin for e in get_channel_allowlist()})


# ---------------------------------------------------------------------------
# Programmatic additions (for dynamic registration / testing)
# ---------------------------------------------------------------------------

_programmatic_entries: list[ChannelAllowlistEntry] = []


def add_channel_to_allowlist(marketplace: str, plugin: str) -> None:
    """Programmatically register a channel plugin in the allowlist.

    These entries are held in-memory only (not persisted) and take
    precedence over GrowthBook entries.  Primarily used for testing
    and for dynamic channel registration at runtime.
    """
    if not marketplace or not plugin:
        raise ValueError("marketplace and plugin must be non-empty strings")
    entry = ChannelAllowlistEntry(
        marketplace=str(marketplace).strip(),
        plugin=str(plugin).strip(),
    )
    if entry not in _programmatic_entries:
        _programmatic_entries.append(entry)
        logger.debug(
            "channel_allowlist: programmatically added %s @ %s",
            plugin,
            marketplace,
        )


def remove_channel_from_allowlist(marketplace: str, plugin: str) -> bool:
    """Remove a programmatically-added channel from the allowlist.

    Returns ``True`` if the entry was found and removed.
    """
    entry = ChannelAllowlistEntry(marketplace=marketplace, plugin=plugin)
    if entry in _programmatic_entries:
        _programmatic_entries.remove(entry)
        return True
    return False


def get_programmatic_entries() -> list[ChannelAllowlistEntry]:
    """Return all programmatically-added allowlist entries."""
    return list(_programmatic_entries)


def clear_programmatic_entries() -> None:
    """Remove all programmatically-added allowlist entries."""
    global _programmatic_entries
    _programmatic_entries = []


def get_effective_allowlist() -> list[ChannelAllowlistEntry]:
    """Return the combined allowlist: programmatic entries + GrowthBook entries.

    Programmatic entries come first, followed by GrowthBook entries
    (excluding any that duplicate a programmatic entry).
    """
    gb_entries = get_channel_allowlist()
    seen: set[tuple[str, str]] = set()
    result: list[ChannelAllowlistEntry] = []

    for entry in _programmatic_entries:
        key = (entry.marketplace, entry.plugin)
        if key not in seen:
            seen.add(key)
            result.append(entry)

    for entry in gb_entries:
        key = (entry.marketplace, entry.plugin)
        if key not in seen:
            seen.add(key)
            result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Bulk allowlist checks
# ---------------------------------------------------------------------------


def filter_allowlisted_plugins(
    plugin_sources: list[str | None],
) -> list[str]:
    """Filter a list of plugin source identifiers, returning only allowlisted ones.

    Non-allowlisted sources are silently dropped.
    """
    return [s for s in plugin_sources if s and is_channel_allowlisted(s)]


def are_all_channels_allowlisted(
    plugin_sources: list[str | None],
) -> bool:
    """Check whether *every* plugin source in the list is allowlisted.

    An empty list returns ``True``.
    """
    if not plugin_sources:
        return True
    return all(
        is_channel_allowlisted(s)
        for s in plugin_sources
    )


def is_any_channel_allowlisted(
    plugin_sources: list[str | None],
) -> bool:
    """Check whether *at least one* plugin source in the list is allowlisted.

    An empty list returns ``False``.
    """
    if not plugin_sources:
        return False
    return any(
        is_channel_allowlisted(s)
        for s in plugin_sources
    )


# ---------------------------------------------------------------------------
# Compatibility aliases (TS port parity)
# ---------------------------------------------------------------------------

# Alias for code that previously called this function under a different name
checkChannelAllowlist = is_channel_allowlisted
getChannelAllowlist = get_channel_allowlist
isChannelsEnabled = is_channels_enabled
