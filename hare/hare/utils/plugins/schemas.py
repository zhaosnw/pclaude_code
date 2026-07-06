"""
Plugin and marketplace JSON schemas — runtime helpers and type aliases.

Port of: src/utils/plugins/schemas.ts

Full Zod validation is not replicated here; use pydantic models or JSON Schema
at integration boundaries. This module preserves constants, guards, and shapes.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict, TypeAlias, Union

# ---------------------------------------------------------------------------
# Official marketplace protection
# ---------------------------------------------------------------------------

ALLOWED_OFFICIAL_MARKETPLACE_NAMES: frozenset[str] = frozenset(
    {
        "hare-code-marketplace",
        "hare-code-plugins",
        "hare-plugins-official",
        "anthropic-marketplace",
        "anthropic-plugins",
        "agent-skills",
        "life-sciences",
        "knowledge-work-plugins",
    }
)

NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES: frozenset[str] = frozenset(
    {"knowledge-work-plugins"}
)

BLOCKED_OFFICIAL_NAME_PATTERN = re.compile(
    r"(?:official[^a-z0-9]*(anthropic|hare)|(?:anthropic|hare)[^a-z0-9]*official|"
    r"^(?:anthropic|hare)[^a-z0-9]*(marketplace|plugins|official))",
    re.IGNORECASE,
)

NON_ASCII_PATTERN = re.compile(r"[^\u0020-\u007E]")

OFFICIAL_GITHUB_ORG = "anthropics"


def is_marketplace_auto_update(marketplace_name: str, entry: dict[str, Any]) -> bool:
    normalized = marketplace_name.lower()
    if "autoUpdate" in entry:
        return bool(entry["autoUpdate"])
    return (
        normalized in ALLOWED_OFFICIAL_MARKETPLACE_NAMES
        and normalized not in NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES
    )


def is_blocked_official_name(name: str) -> bool:
    if name.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES:
        return False
    if NON_ASCII_PATTERN.search(name):
        return True
    return bool(BLOCKED_OFFICIAL_NAME_PATTERN.search(name))


def validate_official_name_source(
    name: str,
    source: dict[str, Any],
) -> str | None:
    normalized_name = name.lower()
    if normalized_name not in ALLOWED_OFFICIAL_MARKETPLACE_NAMES:
        return None

    src = source.get("source")
    if src == "github":
        repo = str(source.get("repo") or "")
        if not repo.lower().startswith(f"{OFFICIAL_GITHUB_ORG}/"):
            return (
                f"The name '{name}' is reserved for official Anthropic marketplaces. "
                f"Only repositories from 'github.com/{OFFICIAL_GITHUB_ORG}/' can use this name."
            )
        return None

    if src == "git" and source.get("url"):
        url = str(source["url"]).lower()
        if "github.com/anthropics/" in url or "git@github.com:anthropics/" in url:
            return None
        return (
            f"The name '{name}' is reserved for official Anthropic marketplaces. "
            f"Only repositories from 'github.com/{OFFICIAL_GITHUB_ORG}/' can use this name."
        )

    return (
        f"The name '{name}' is reserved for official Anthropic marketplaces and can only be used "
        f"with GitHub sources from the '{OFFICIAL_GITHUB_ORG}' organization."
    )


# ---------------------------------------------------------------------------
# Plugin / marketplace source helpers
# ---------------------------------------------------------------------------

PluginSource: TypeAlias = Union[str, dict[str, Any]]
MarketplaceSource: TypeAlias = dict[str, Any]


def is_local_plugin_source(source: PluginSource) -> bool:
    return isinstance(source, str) and source.startswith("./")


def is_local_marketplace_source(source: MarketplaceSource) -> bool:
    return source.get("source") in ("file", "directory")


# ---------------------------------------------------------------------------
# TypedDict sketches (incomplete — extend for strict validation)
# ---------------------------------------------------------------------------

PluginScope = Literal["managed", "user", "project", "local"]


class InstalledPluginV1(TypedDict, total=False):
    version: str
    installedAt: str
    lastUpdated: str
    installPath: str
    gitCommitSha: str


class PluginInstallationEntry(TypedDict, total=False):
    scope: PluginScope
    projectPath: str
    installPath: str
    version: str
    installedAt: str
    lastUpdated: str
    gitCommitSha: str


class InstalledPluginsFileV2(TypedDict):
    version: Literal[2]
    plugins: dict[str, list[PluginInstallationEntry]]


# Schema placeholders for JSON-schema / pydantic wiring
PluginManifestSchema: Any = None
PluginMarketplaceSchema: Any = None
MarketplaceSourceSchema: Any = None
PluginSourceSchema: Any = None
KnownMarketplacesFileSchema: Any = None
InstalledPluginsFileSchema: Any = None
DependencyRefSchema: Any = None
PluginHooksSchema: Any = None
LspServerConfigSchema: Any = None
CommandMetadataSchema: Any = None
