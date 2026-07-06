"""
Plugin types — manifest, installation, marketplace, and error types.

Port of: src/types/plugin.ts (364 lines)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union


class PluginErrorKind(str, Enum):
    """Discriminated union variants for plugin errors."""

    NOT_FOUND = "not_found"
    ALREADY_INSTALLED = "already_installed"
    INVALID_MANIFEST = "invalid_manifest"
    MISSING_DEPENDENCY = "missing_dependency"
    INCOMPATIBLE_VERSION = "incompatible_version"
    INSTALL_FAILED = "install_failed"
    UNINSTALL_FAILED = "uninstall_failed"
    ENABLE_FAILED = "enable_failed"
    DISABLE_FAILED = "disable_failed"
    VALIDATION_FAILED = "validation_failed"
    MARKETPLACE_ERROR = "marketplace_error"
    NETWORK_ERROR = "network_error"
    PERMISSION_DENIED = "permission_denied"
    HOOK_ERROR = "hook_error"
    COMMAND_CONFLICT = "command_conflict"
    AGENT_CONFLICT = "agent_conflict"
    MCP_CONFLICT = "mcp_conflict"
    SOURCE_ERROR = "source_error"
    UNKNOWN = "unknown"


class PluginError(Exception):
    """Plugin error with discriminated kind."""

    def __init__(
        self,
        message: str,
        kind: PluginErrorKind = PluginErrorKind.UNKNOWN,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


PluginSource = Literal["marketplace", "local", "user", "project", "builtin"]


@dataclass
class PluginPermission:
    tool_name: str
    description: str = ""


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.1"
    description: str = ""
    author: str = ""
    homepage: str = ""
    license: str = ""
    repository: str = ""
    permissions: list[PluginPermission] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, str]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)
    min_claude_version: str = ""
    force_for_plugin: bool = False


@dataclass
class InstalledPlugin:
    name: str
    path: str
    manifest: PluginManifest
    enabled: bool = True
    source: PluginSource = "local"


@dataclass
class LoadedPlugin:
    """Fully loaded plugin with runtime state."""

    name: str = ""
    path: str = ""
    manifest: PluginManifest = field(default_factory=lambda: PluginManifest(name=""))
    enabled: bool = True
    source: PluginSource = "local"
    plugin_root: str = ""
    commands: list[dict[str, Any]] = field(default_factory=list)
    agents: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    output_styles: list[dict[str, Any]] = field(default_factory=list)
    has_load_error: bool = False
    load_error: Optional[str] = None


@dataclass
class PluginLoadResult:
    """Result of loading plugins from a directory."""

    loaded: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)


@dataclass
class BuiltinPluginDefinition:
    """Definition for a built-in plugin."""

    name: str = ""
    description: str = ""
    plugin_root: str = ""


@dataclass
class PluginConfig:
    """Plugin configuration from settings."""

    enabled_plugins: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    marketplace_sources: list[str] = field(default_factory=list)


@dataclass
class PluginRepository:
    """Plugin marketplace repository."""

    name: str = ""
    url: str = ""
    description: str = ""
    plugins: list[dict[str, Any]] = field(default_factory=list)


def get_plugin_error_message(error: PluginError) -> str:
    """Human-readable error message for each error kind."""
    messages = {
        PluginErrorKind.NOT_FOUND: "Plugin not found.",
        PluginErrorKind.ALREADY_INSTALLED: "Plugin is already installed.",
        PluginErrorKind.INVALID_MANIFEST: "Plugin manifest is invalid.",
        PluginErrorKind.INSTALL_FAILED: "Failed to install plugin.",
        PluginErrorKind.UNINSTALL_FAILED: "Failed to uninstall plugin.",
        PluginErrorKind.VALIDATION_FAILED: "Plugin validation failed.",
        PluginErrorKind.MARKETPLACE_ERROR: "Marketplace operation failed.",
        PluginErrorKind.NETWORK_ERROR: "Network error contacting marketplace.",
        PluginErrorKind.PERMISSION_DENIED: "Plugin permission denied.",
        PluginErrorKind.HOOK_ERROR: "Plugin hook execution failed.",
        PluginErrorKind.COMMAND_CONFLICT: "Plugin command name conflicts with existing.",
        PluginErrorKind.UNKNOWN: "Unknown plugin error.",
    }
    return messages.get(error.kind, str(error))
