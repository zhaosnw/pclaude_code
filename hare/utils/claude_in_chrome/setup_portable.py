"""Portable Chrome native-messaging setup and extension detection (bundled extension).

Port of: src/utils/claudeInChrome/setupPortable.ts
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from hare.utils.claude_in_chrome.common import (
    BROWSER_DETECTION_ORDER,
    CHROMIUM_BROWSERS,
    ChromiumBrowser,
    get_all_browser_data_paths,
    get_extension_ids,
)
from hare.utils.log import log_error_msg

CHROME_EXTENSION_URL = "https://claude.ai/chrome"

Logger = Callable[[str], None]


def install_portable_native_host(manifest_dir: Path) -> bool:
    """Install the native host manifest into a portable directory.

    Used for portable/bundled setups where the extension ships alongside the app.

    Args:
        manifest_dir: Directory to write the native host manifest into.

    Returns:
        True if manifest was written successfully.
    """
    from hare.utils.claude_in_chrome.common import (
        NATIVE_HOST_IDENTIFIER,
        NATIVE_HOST_MANIFEST_NAME,
        get_allowed_origins,
    )

    import json

    manifest = {
        "name": NATIVE_HOST_IDENTIFIER,
        "description": "Claude Code Browser Extension Native Host",
        "path": str(manifest_dir / "chrome-native-host"),
        "type": "stdio",
        "allowed_origins": get_allowed_origins(),
    }

    manifest_path = manifest_dir / NATIVE_HOST_MANIFEST_NAME
    manifest_content = json.dumps(manifest, indent=2, ensure_ascii=False)

    try:
        existing = manifest_path.read_text("utf-8") if manifest_path.exists() else None
        if existing == manifest_content:
            return True
    except (OSError, UnicodeDecodeError):
        pass

    try:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest_content, "utf-8")
        return True
    except OSError as e:
        log_error_msg(f"[Claude in Chrome] Failed to write portable manifest: {e}")
        return False


async def detect_extension_installation(
    browser_paths: list[dict],
    log: Logger | None = None,
) -> bool:
    """Detect if the Claude in Chrome extension is installed.

    Scans Extensions directories across all supported Chromium browsers
    and their profiles to check if any of the known extension IDs exist.

    Args:
        browser_paths: List of {browser: str, path: str} dicts from get_all_browser_data_paths.
        log: Optional debug logging callback.

    Returns:
        True if the extension is found in any browser profile.
    """
    if not browser_paths:
        if log:
            log("[Claude in Chrome] No browser paths to check")
        return False

    extension_ids = get_extension_ids()

    for entry in browser_paths:
        browser: ChromiumBrowser = entry["browser"]  # type: ignore[assignment]
        browser_base = Path(entry["path"])

        # Read browser profile directories
        try:
            profile_entries = list(browser_base.iterdir())
        except (OSError, PermissionError):
            # Browser not installed or path doesn't exist
            continue

        profile_dirs = [
            p.name
            for p in profile_entries
            if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
        ]

        if profile_dirs and log:
            log(f"[Claude in Chrome] Found {browser} profiles: {', '.join(profile_dirs)}")

        # Check each profile for each extension ID
        for profile in profile_dirs:
            for ext_id in extension_ids:
                ext_path = browser_base / profile / "Extensions" / ext_id
                if ext_path.is_dir():
                    if log:
                        log(f"[Claude in Chrome] Extension {ext_id} found in {browser} {profile}")
                    return True

    if log:
        log("[Claude in Chrome] Extension not found in any browser")
    return False


async def is_chrome_extension_installed_portable(
    browser_paths: list[dict],
    log: Logger | None = None,
) -> bool:
    """Simple wrapper that returns just the boolean result for extension detection.

    Args:
        browser_paths: Browser data paths to scan.
        log: Optional debug logger.

    Returns:
        True if extension is installed in any browser.
    """
    return await detect_extension_installation(browser_paths, log)


async def is_chrome_extension_installed(
    log: Logger | None = None,
) -> bool:
    """Check if Chrome extension is installed using auto-detected browser paths.

    Args:
        log: Optional debug logger.

    Returns:
        True if extension is found in any supported browser.
    """
    browser_paths = get_all_browser_data_paths()
    return await is_chrome_extension_installed_portable(browser_paths, log)
