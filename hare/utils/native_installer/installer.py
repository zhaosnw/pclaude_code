"""
Native installer implementation.

Port of: src/utils/nativeInstaller/installer.ts (stub surface).
"""

from __future__ import annotations

from typing import Literal, TypedDict


class SetupMessage(TypedDict):
    message: str
    userActionRequired: bool
    type: Literal["path", "alias", "info", "error"]


VERSION_RETENTION_COUNT = 2


async def check_install() -> dict[str, bool]:
    """Check if Hare is properly installed. Stub."""
    return {"installed": True, "up_to_date": True}


async def install_latest() -> bool:
    """Install or update to latest version. Stub."""
    return True


async def cleanup_npm_installations() -> None:
    """Remove stale npm-based installs. Stub."""
    return


async def cleanup_old_versions() -> None:
    """Prune old version directories. Stub."""
    return


async def cleanup_shell_aliases() -> None:
    """Remove obsolete shell aliases. Stub."""
    return


async def lock_current_version() -> None:
    """Pin active version for multi-process safety. Stub."""
    return


async def remove_installed_symlink() -> None:
    """Remove user-level `hare` symlink. Stub."""
    return
