"""Resolve selected Teleport environment from settings.

Port of: src/utils/teleport/environmentSelection.ts

Priority chain for selecting an environment:
  1. Valid environments are fetched from the Sessions API.
  2. Bridge environments are filtered out if any non-bridge environment exists
     (bridge environments are only selected if they are the ONLY option).
  3. If the user has set a default environment ID in settings, it is used.
  4. Otherwise the first available non-bridge environment is selected.
  5. The settings source that provided the default is tracked for diagnostics.

SettingSource priority for defaultEnvironmentId (highest to lowest):
  policySettings → localSettings → projectSettings → userSettings → flagSettings

The defaultEnvironmentId is read from settings.remote.defaultEnvironmentId.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hare.utils.settings.constants import SettingSource, SETTING_SOURCES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Re-export SettingSource for convenience (matching the port target)
# ---------------------------------------------------------------------------

# The original TS file defines its own SettingSource union. We re-export the
# canonical one from constants to avoid duplication and drift.
SettingSource = SettingSource  # re-export
__all__ = [
    "EnvironmentResource",
    "EnvironmentSelectionInfo",
    "fetch_environments",
    "get_environment_selection_info",
    "resolve_default_environment_id",
    "filter_bridge_environments",
    "select_environment",
    "validate_environment_resource",
    "clear_environment_cache",
    "BRIDGE_KIND",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRIDGE_KIND = "bridge"

# Setting sources that are checked for defaultEnvironmentId, in priority order
# (highest first — first non-empty value wins).
DEFAULT_ENV_SOURCE_PRIORITY: list[SettingSource] = [
    "policySettings",
    "localSettings",
    "projectSettings",
    "userSettings",
    "flagSettings",
]

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentResource:
    """A single Teleport environment from the Sessions API.

    Attributes
    ----------
    environment_id: Unique identifier for the environment.
    kind: Environment kind (e.g. "local", "remote", "bridge").
    name: Human-readable display name.
    """

    environment_id: str
    kind: str
    name: str = ""

    def __post_init__(self) -> None:
        if not self.environment_id or not self.environment_id.strip():
            raise ValueError("EnvironmentResource.environment_id must not be empty")
        if not self.kind or not self.kind.strip():
            raise ValueError("EnvironmentResource.kind must not be empty")

    def is_bridge(self) -> bool:
        """Return True if this is a bridge environment."""
        return self.kind == BRIDGE_KIND

    def to_dict(self) -> dict[str, str]:
        """Serialize to a plain dict for debugging/logging."""
        return {
            "environment_id": self.environment_id,
            "kind": self.kind,
            "name": self.name,
        }


@dataclass
class EnvironmentSelectionInfo:
    """Result of environment selection.

    Attributes
    ----------
    available_environments: All environments fetched from the API.
    selected_environment: The chosen environment (or None if no environments exist).
    selected_environment_source: Which settings source provided the default
        environment ID override (None if no override was found or no setting
        source was checked).
    """

    available_environments: list[EnvironmentResource]
    selected_environment: EnvironmentResource | None
    selected_environment_source: SettingSource | None

    @property
    def has_environments(self) -> bool:
        """Return True if any environments are available."""
        return len(self.available_environments) > 0

    @property
    def has_selection(self) -> bool:
        """Return True if a specific environment was selected."""
        return self.selected_environment is not None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_environment_resource(raw: dict[str, Any]) -> EnvironmentResource | None:
    """Validate and coerce a raw API response dict into an EnvironmentResource.

    Returns None if the dict is missing required fields or has invalid types.
    Logs a warning for malformed entries so callers can diagnose API issues.

    Edge cases handled:
      - Missing ``environment_id`` or ``kind`` → None (skipped).
      - Non-string values for ``environment_id`` or ``kind`` → None.
      - Empty string ``environment_id`` or ``kind`` → None.
      - Missing ``name`` → empty string default.
      - Non-string ``name`` → cast to str.
    """
    if not isinstance(raw, dict):
        logger.warning("validate_environment_resource: expected dict, got %s", type(raw).__name__)
        return None

    env_id = raw.get("environment_id") or raw.get("id") or raw.get("environmentId")
    kind = raw.get("kind") or raw.get("type")

    # Required fields must be non-empty strings
    if not isinstance(env_id, str) or not env_id.strip():
        logger.warning("validate_environment_resource: missing or invalid environment_id in %r", raw)
        return None
    if not isinstance(kind, str) or not kind.strip():
        logger.warning("validate_environment_resource: missing or invalid kind in %r", raw)
        return None

    name = raw.get("name", "")
    if not isinstance(name, str):
        name = str(name)

    return EnvironmentResource(
        environment_id=env_id.strip(),
        kind=kind.strip(),
        name=name.strip(),
    )


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _get_settings_deprecated() -> dict[str, Any]:
    """Get merged settings from all sources (legacy entry point).

    Wires through to the canonical settings loader.  The "deprecated" naming
    matches the TS source where this function was kept for backward compat
    while the codebase migrated to the per-source API.
    """
    try:
        from hare.utils.settings.settings import get_settings_deprecated as _gsd

        return _gsd()
    except ImportError:
        logger.debug("_get_settings_deprecated: settings module not available, returning {}")
        return {}


def _get_settings_for_source(source: SettingSource) -> dict[str, Any] | None:
    """Get settings for a single source (uncached read).

    Returns None if the source has no settings file or the file cannot be read.
    """
    try:
        from hare.utils.settings.settings import get_settings_for_source as _gsfs

        return _gsfs(source)
    except ImportError:
        logger.debug("_get_settings_for_source: settings module not available, returning None")
        return None


def resolve_default_environment_id() -> tuple[str | None, SettingSource | None]:
    """Walk settings sources in priority order to find a defaultEnvironmentId.

    Returns
    -------
    (environment_id, source)
        *environment_id* is the first non-empty string found, or None.
        *source* is the SettingSource that provided it, or None.
    """
    for source in DEFAULT_ENV_SOURCE_PRIORITY:
        settings = _get_settings_for_source(source)
        if not settings:
            continue
        remote = settings.get("remote")
        if not isinstance(remote, dict):
            continue
        default_id = remote.get("defaultEnvironmentId")
        if isinstance(default_id, str) and default_id.strip():
            return default_id.strip(), source

    # Fallback: check deprecated merged settings (lowest priority)
    merged = _get_settings_deprecated()
    if merged:
        remote = merged.get("remote")
        if isinstance(remote, dict):
            default_id = remote.get("defaultEnvironmentId")
            if isinstance(default_id, str) and default_id.strip():
                return default_id.strip(), None

    return None, None


# ---------------------------------------------------------------------------
# Environment fetching
# ---------------------------------------------------------------------------

def _normalize_api_environments(raw_list: list[dict[str, Any]]) -> list[EnvironmentResource]:
    """Convert a raw API response list into validated EnvironmentResource objects.

    Invalid entries are silently dropped with a debug log.
    """
    result: list[EnvironmentResource] = []
    for item in raw_list:
        env = validate_environment_resource(item)
        if env is not None:
            result.append(env)
    return result


# Cached result for fetch_environments (session-level cache)
_cached_environments: list[EnvironmentResource] | None = None
_cache_valid: bool = False


def clear_environment_cache() -> None:
    """Clear the environment fetch cache. Call when auth state changes."""
    global _cached_environments, _cache_valid
    _cached_environments = None
    _cache_valid = False


async def fetch_environments(*, force_refresh: bool = False) -> list[EnvironmentResource]:
    """Fetch available Teleport environments from the Sessions API.

    Results are cached for the lifetime of the session unless *force_refresh*
    is True.  The cache can also be cleared with :func:`clear_environment_cache`.

    Returns an empty list on any error (network, auth, parse).
    """
    global _cached_environments, _cache_valid

    if _cache_valid and _cached_environments is not None and not force_refresh:
        return _cached_environments

    _cached_environments = []
    _cache_valid = False

    try:
        from hare.utils.teleport.api import fetch_code_sessions, prepare_api_request
    except ImportError:
        logger.debug("fetch_environments: teleport API module not available")
        return []

    # 1. Validate auth material. Bail early if no credentials.
    try:
        auth = await prepare_api_request()
    except Exception:
        logger.debug("fetch_environments: prepare_api_request raised", exc_info=True)
        return []

    access_token = auth.get("accessToken", "")
    if not access_token:
        logger.debug("fetch_environments: no access token, skipping API call")
        return []

    # 2. Call the Sessions API.
    try:
        raw_sessions = await fetch_code_sessions()
    except Exception:
        logger.warning("fetch_environments: fetch_code_sessions raised", exc_info=True)
        return []

    if not raw_sessions:
        logger.debug("fetch_environments: API returned empty list")
        return []

    # 3. Normalize and validate.
    environments = _normalize_api_environments(raw_sessions)

    _cached_environments = environments
    _cache_valid = True
    return environments


# ---------------------------------------------------------------------------
# Environment filtering and selection logic
# ---------------------------------------------------------------------------

def filter_bridge_environments(
    environments: list[EnvironmentResource],
) -> list[EnvironmentResource]:
    """Filter out bridge environments, unless they are the ONLY option.

    Bridge environments are maintenance conduits, not user-facing targets.
    They are only returned as a last resort when no other environment is
    available.

    Parameters
    ----------
    environments: The full list of environments from the API.

    Returns
    -------
    A filtered list: non-bridge environments when any exist, otherwise
    the original list (bridge-only).
    """
    if not environments:
        return []

    non_bridge = [e for e in environments if not e.is_bridge()]
    if non_bridge:
        return non_bridge
    # Only bridge environments exist — return them as a fallback.
    return list(environments)


def _find_by_id(
    environments: list[EnvironmentResource],
    environment_id: str,
) -> EnvironmentResource | None:
    """Find an environment by its ID. Returns None if not found."""
    for env in environments:
        if env.environment_id == environment_id:
            return env
    return None


def select_environment(
    environments: list[EnvironmentResource],
    preferred_id: str | None = None,
) -> EnvironmentResource | None:
    """Select the best environment from a list.

    Selection rules (in priority order):
      1. If *preferred_id* is given and matches an environment, select it.
      2. Otherwise, select the first environment in the list.
      3. If the list is empty, return None.

    Parameters
    ----------
    environments: Non-bridge environments (caller should filter first).
    preferred_id: A default environment ID from settings.

    Returns
    -------
    The selected EnvironmentResource, or None.
    """
    if not environments:
        return None

    if preferred_id:
        match = _find_by_id(environments, preferred_id)
        if match is not None:
            return match
        # Preferred ID not found in the current list — log and fall through
        # to the first-available fallback.
        logger.debug(
            "select_environment: preferred_id %r not found in %d environments, "
            "falling back to first available",
            preferred_id,
            len(environments),
        )

    return environments[0]


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def get_environment_selection_info() -> EnvironmentSelectionInfo:
    """Resolve the selected Teleport environment with full priority chain.

    Full flow:
      1. Fetch available environments from the Sessions API.
      2. If none are available, return an empty selection info.
      3. Filter out bridge environments (unless they are the only option).
      4. Walk settings sources to find a ``defaultEnvironmentId`` override.
      5. Select the named environment if found, otherwise the first available.
      6. Return the result with source tracking for diagnostics.

    Error handling:
      - API failures → empty environments list (graceful degradation).
      - Malformed API responses → individual entries skipped with warning.
      - Missing settings → treated as no override.
      - Preferred ID pointing to a missing environment → fallback to first.

    Returns
    -------
    EnvironmentSelectionInfo with available environments, the selected one,
    and the settings source of the override (if any).
    """
    # 1. Fetch environments.
    environments = await fetch_environments()
    if not environments:
        logger.debug("get_environment_selection_info: no environments available")
        return EnvironmentSelectionInfo(
            available_environments=[],
            selected_environment=None,
            selected_environment_source=None,
        )

    # 2. Filter bridge environments.
    filtered = filter_bridge_environments(environments)

    # 3. Resolve default override from settings.
    default_id, source = resolve_default_environment_id()

    # 4. Select.
    selected = select_environment(filtered, preferred_id=default_id)

    # 5. Log selection for diagnostics.
    if selected is not None:
        logger.debug(
            "get_environment_selection_info: selected %s (kind=%s) from %d environments, "
            "source=%s, default_id=%s",
            selected.environment_id,
            selected.kind,
            len(filtered),
            source,
            default_id,
        )
    else:
        logger.debug(
            "get_environment_selection_info: no selection from %d environments "
            "(filtered from %d total)",
            len(filtered),
            len(environments),
        )

    return EnvironmentSelectionInfo(
        available_environments=environments,
        selected_environment=selected,
        selected_environment_source=source,
    )
