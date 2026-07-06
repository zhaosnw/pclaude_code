"""
Public barrel for native installer — mirrors nativeInstaller/index.ts.
"""

from __future__ import annotations

from hare.utils.native_installer.installer import (
    VERSION_RETENTION_COUNT,
    SetupMessage,
    check_install,
    cleanup_npm_installations,
    cleanup_old_versions,
    cleanup_shell_aliases,
    install_latest,
    lock_current_version,
    remove_installed_symlink,
)

__all__ = [
    "VERSION_RETENTION_COUNT",
    "SetupMessage",
    "check_install",
    "cleanup_npm_installations",
    "cleanup_old_versions",
    "cleanup_shell_aliases",
    "install_latest",
    "lock_current_version",
    "remove_installed_symlink",
]
