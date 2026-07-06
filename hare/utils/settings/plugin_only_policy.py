"""Port of: src/utils/settings/pluginOnlyPolicy.ts

Enforces strict plugin-only customization policy for enterprise environments.
When an admin sets `strictPluginOnlyCustomization` in managed/policy settings,
user-level (~/.claude/*) and project-level (.claude/*) customization sources
are blocked for the listed surfaces.

Lockable customization surfaces: skills, agents, hooks, mcp.

Design (matching TS):
  - `true` locks all four surfaces; array form locks only those listed.
  - Absent/undefined -> nothing locked (the default).
  - Managed (policySettings) and plugin sources are always allowed —
    managed settings are admin-controlled, plugins are gated separately
    via `strictKnownMarketplaces`.
  - Forward-compat: unknown surface names in the array are silently dropped
    so a future enum value (e.g. 'commands') on an old client degrades to
    less-locked, never to everything-unlocked.

Admin-trusted sources (always bypass the restriction):
  plugin, policySettings, built-in, builtin, bundled

Composes with:
  - strictKnownMarketplaces (plugins gated by marketplace allowlist)
  - allowManagedHooksOnly (managed hooks only)
  - strictMCPToolApproval (managed MCP servers only)
"""

from __future__ import annotations

from typing import Literal, overload

# ---------------------------------------------------------------------------
# Customization surfaces (matching TS CUSTOMIZATION_SURFACES)
# ---------------------------------------------------------------------------

CUSTOMIZATION_SURFACES: tuple[str, ...] = (
    "skills",
    "agents",
    "hooks",
    "mcp",
)

CustomizationSurface = Literal["skills", "agents", "hooks", "mcp"]

# ---------------------------------------------------------------------------
# Admin-trusted sources (matching TS ADMIN_TRUSTED_SOURCES)
# ---------------------------------------------------------------------------

# These sources bypass the strictPluginOnlyCustomization restriction.
# Rationale (matching TS):
#   - plugin: gated separately by strictKnownMarketplaces
#   - policySettings: from managed settings, admin-controlled by definition
#   - built-in / builtin / bundled: ship with the CLI, not user-authored
#
# Everything else (userSettings, projectSettings, localSettings, flagSettings,
# mcp, undefined) is user-controlled and blocked when the surface is locked.
# Covers AgentDefinition.source ('built-in' with hyphen) and Command.source
# ('builtin' no hyphen, plus 'bundled').

ADMIN_TRUSTED_SOURCES: frozenset[str] = frozenset(
    {
        "plugin",
        "policySettings",
        "built-in",
        "builtin",
        "bundled",
    }
)

# ---------------------------------------------------------------------------
# Policy value retrieval
# ---------------------------------------------------------------------------


def _get_strict_plugin_only_customization_raw() -> (
    bool | list[str] | None
):
    """Read strictPluginOnlyCustomization from policySettings, raw.

    Returns None when not set (no policy active).
    Returns the raw value otherwise (True, False, or list[str]).
    """
    from hare.utils.settings.settings import get_settings_for_source

    policy = get_settings_for_source("policySettings")
    if not policy:
        return None
    val = policy.get("strictPluginOnlyCustomization")
    if val is None:
        return None
    return val


# ---------------------------------------------------------------------------
# Core policy checks
# ---------------------------------------------------------------------------


@overload
def is_restricted_to_plugin_only(surface: CustomizationSurface) -> bool: ...


@overload
def is_restricted_to_plugin_only(surface: str) -> bool: ...


def is_restricted_to_plugin_only(surface: str = "") -> bool:
    """Check if a customization surface is locked to plugin-only sources.

    TS: isRestrictedToPluginOnly — reads strictPluginOnlyCustomization from
    policySettings.

    - True -> all four surfaces locked
    - [surface_name, ...] -> only listed surfaces locked
    - Absent/None/False/empty -> nothing locked (the default)

    Locked surfaces only trust sources: plugin, policySettings, built-in,
    builtin, bundled. User-level (~/.claude/*) and project-level (.claude/*)
    customizations are blocked.

    Pattern at call sites:
        if is_restricted_to_plugin_only("hooks"):
            # skip user/project hook sources
            ...
    """
    val = _get_strict_plugin_only_customization_raw()
    if val is None or val is False:
        return False
    if val is True:
        return True
    if isinstance(val, list):
        return surface in val
    # Invalid value (e.g. string, number): degrade to unlocked.
    # Matching TS .catch(undefined) — never break everything for one field.
    return False


def is_source_admin_trusted(source: str | None) -> bool:
    """Check if a customization source is admin-trusted.

    TS: isSourceAdminTrusted

    Use this to gate frontmatter-hook registration and similar per-item
    checks where the item carries a source tag but the surface's filesystem
    loader already ran.

    Pattern at call sites:
        allowed = (
            not is_restricted_to_plugin_only(surface)
            or is_source_admin_trusted(item.source)
        )
        if item.hooks and allowed:
            register(...)
    """
    if source is None:
        return False
    return source in ADMIN_TRUSTED_SOURCES


# ---------------------------------------------------------------------------
# Derived policy queries
# ---------------------------------------------------------------------------


def is_any_surface_restricted() -> bool:
    """Check if any customization surface is currently locked.

    Useful for UI hints ("Some customizations blocked by admin policy").
    """
    val = _get_strict_plugin_only_customization_raw()
    if val is True:
        return True
    if isinstance(val, list) and len(val) > 0:
        return True
    return False


def get_restricted_surfaces() -> frozenset[str]:
    """Return the set of surfaces currently locked by the policy.

    - Empty frozenset: no surfaces locked (default)
    - frozenset with all four: True was set
    - frozenset with subset: array form with specific surfaces

    Forward-compat: unknown surface names in the policy are filtered out
    (matching TS preprocess that drops unknowns so a future value like
    'commands' degrades to less-locked, never to everything-unlocked).
    """
    val = _get_strict_plugin_only_customization_raw()
    if val is True:
        return frozenset(CUSTOMIZATION_SURFACES)
    if isinstance(val, list):
        return frozenset(
            s for s in val if s in CUSTOMIZATION_SURFACES
        )
    return frozenset()


def get_strict_plugin_only_customization() -> bool | list[str]:
    """Get the full strictPluginOnlyCustomization policy value.

    TS: reads strictPluginOnlyCustomization from policySettings directly.

    This is the canonical accessor used by settings.py and other modules.
    Returns False when no policy is active.
    """
    val = _get_strict_plugin_only_customization_raw()
    if val is None:
        return False
    if isinstance(val, list):
        return val
    return bool(val)


# ---------------------------------------------------------------------------
# Combined policy check
# ---------------------------------------------------------------------------


def is_source_blocked_for_surface(
    source: str | None,
    surface: str,
) -> bool:
    """Check if a given source is blocked for a given surface.

    Returns True when:
      1. The surface is locked by strictPluginOnlyCustomization, AND
      2. The source is NOT admin-trusted.

    Returns False when:
      - The surface is not locked (policy not active for this surface), OR
      - The source is admin-trusted (always allowed).

    Convenience combining is_restricted_to_plugin_only + is_source_admin_trusted.
    """
    if not is_restricted_to_plugin_only(surface):
        return False
    return not is_source_admin_trusted(source)


# ---------------------------------------------------------------------------
# Strict agent-only lockdown (stricter variant)
# ---------------------------------------------------------------------------


def is_restricted_to_builtin_agents_only() -> bool:
    """Check if only built-in agents are allowed (stricter than plugin-only).

    TS-related: strictPluginOnlyCustomization for 'agents' combined with
    strictKnownMarketplaces that excludes all marketplace plugins effectively
    means only built-in agents survive.

    Returns True when 'agents' is locked AND either:
      - strictKnownMarketplaces is empty, OR
      - the policy explicitly sets agentsPluginOnly: true
    """
    if not is_restricted_to_plugin_only("agents"):
        return False

    from hare.utils.settings.settings import get_settings_for_source

    policy = get_settings_for_source("policySettings")
    if not policy:
        return is_restricted_to_plugin_only("agents")

    # If strictKnownMarketplaces is explicitly empty, no external plugins
    # can provide agents -> only built-in remain.
    marketplaces = policy.get("strictKnownMarketplaces")
    if isinstance(marketplaces, list) and len(marketplaces) == 0:
        return True

    # Explicit flag
    if policy.get("agentsPluginOnly") is True:
        return True

    return False


# ---------------------------------------------------------------------------
# Validate a policy value (for schema/Doctor use)
# ---------------------------------------------------------------------------


def validate_strict_plugin_only_policy_value(
    value: object,
) -> tuple[bool, str | None]:
    """Validate a strictPluginOnlyCustomization value.

    Returns (is_valid, error_message).
    - True/False: valid
    - list of strings matching CustomizationSurface: valid
    - list with unknown surfaces: valid but warn (unknown surfaces dropped)
    - anything else: invalid
    """
    if isinstance(value, bool):
        return (True, None)

    if isinstance(value, list):
        unknown = [s for s in value if s not in CUSTOMIZATION_SURFACES]
        if unknown:
            return (
                True,
                f"Unknown surfaces ignored: {', '.join(map(str, unknown))}. "
                f"Valid surfaces: {', '.join(CUSTOMIZATION_SURFACES)}.",
            )
        return (True, None)

    return (
        False,
        f"Expected boolean or array of surfaces, got {type(value).__name__}. "
        "Policy will degrade to unlocked.",
    )
