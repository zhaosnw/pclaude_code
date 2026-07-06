"""Port of: src/utils/claudeInChrome/common.ts"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

CHROME_EXTENSION_ID = "anthropic-hare-code-chrome"
NATIVE_HOST_NAME = "com.anthropic.hare_code"

# Native messaging host identifier (matches Chrome extension manifest)
NATIVE_HOST_IDENTIFIER = "com.anthropic.claude_code_browser_extension"
NATIVE_HOST_MANIFEST_NAME = f"{NATIVE_HOST_IDENTIFIER}.json"

# Extension IDs (production + internal dev/ant)
PROD_EXTENSION_ID = "fcoeoabgfenejglbffodgkkbkcdhcgfn"
DEV_EXTENSION_ID = "dihbgbndebgnbjfmelmegjepbnkhlgni"
ANT_EXTENSION_ID = "dngcpimnedloihjnnfngkgjoidhnaolf"

# MCP server name
CLAUDE_IN_CHROME_MCP_SERVER_NAME = "claude-in-chrome"

# Chrome reconnect URL shown after first-time manifest install
CHROME_EXTENSION_RECONNECT_URL = "https://clau.de/chrome/reconnect"

ChromiumBrowser = Literal[
    "chrome", "brave", "arc", "chromium", "edge", "vivaldi", "opera"
]

BROWSER_DETECTION_ORDER: list[ChromiumBrowser] = [
    "chrome",
    "brave",
    "arc",
    "edge",
    "chromium",
    "vivaldi",
    "opera",
]

BrowserConfig = dict

CHROMIUM_BROWSERS: dict[ChromiumBrowser, BrowserConfig] = {
    "chrome": {
        "name": "Google Chrome",
        "macos": {
            "app_name": "Google Chrome",
            "data_path": ["Library", "Application Support", "Google", "Chrome"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "Google",
                "Chrome",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["google-chrome", "google-chrome-stable"],
            "data_path": [".config", "google-chrome"],
            "native_messaging_path": [".config", "google-chrome", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["Google", "Chrome", "User Data"],
            "registry_key": "HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts",
        },
    },
    "brave": {
        "name": "Brave",
        "macos": {
            "app_name": "Brave Browser",
            "data_path": ["Library", "Application Support", "BraveSoftware", "Brave-Browser"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "BraveSoftware",
                "Brave-Browser",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["brave-browser", "brave"],
            "data_path": [".config", "BraveSoftware", "Brave-Browser"],
            "native_messaging_path": [".config", "BraveSoftware", "Brave-Browser", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["BraveSoftware", "Brave-Browser", "User Data"],
            "registry_key": "HKCU\\Software\\BraveSoftware\\Brave-Browser\\NativeMessagingHosts",
        },
    },
    "arc": {
        "name": "Arc",
        "macos": {
            "app_name": "Arc",
            "data_path": ["Library", "Application Support", "Arc", "User Data"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "Arc",
                "User Data",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": [],
            "data_path": [],
            "native_messaging_path": [],
        },
        "windows": {
            "data_path": ["Arc", "User Data"],
            "registry_key": "HKCU\\Software\\ArcBrowser\\Arc\\NativeMessagingHosts",
        },
    },
    "chromium": {
        "name": "Chromium",
        "macos": {
            "app_name": "Chromium",
            "data_path": ["Library", "Application Support", "Chromium"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "Chromium",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["chromium", "chromium-browser"],
            "data_path": [".config", "chromium"],
            "native_messaging_path": [".config", "chromium", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["Chromium", "User Data"],
            "registry_key": "HKCU\\Software\\Chromium\\NativeMessagingHosts",
        },
    },
    "edge": {
        "name": "Microsoft Edge",
        "macos": {
            "app_name": "Microsoft Edge",
            "data_path": ["Library", "Application Support", "Microsoft Edge"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "Microsoft Edge",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["microsoft-edge", "microsoft-edge-stable"],
            "data_path": [".config", "microsoft-edge"],
            "native_messaging_path": [".config", "microsoft-edge", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["Microsoft", "Edge", "User Data"],
            "registry_key": "HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts",
        },
    },
    "vivaldi": {
        "name": "Vivaldi",
        "macos": {
            "app_name": "Vivaldi",
            "data_path": ["Library", "Application Support", "Vivaldi"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "Vivaldi",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["vivaldi", "vivaldi-stable"],
            "data_path": [".config", "vivaldi"],
            "native_messaging_path": [".config", "vivaldi", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["Vivaldi", "User Data"],
            "registry_key": "HKCU\\Software\\Vivaldi\\NativeMessagingHosts",
        },
    },
    "opera": {
        "name": "Opera",
        "macos": {
            "app_name": "Opera",
            "data_path": ["Library", "Application Support", "com.operasoftware.Opera"],
            "native_messaging_path": [
                "Library",
                "Application Support",
                "com.operasoftware.Opera",
                "NativeMessagingHosts",
            ],
        },
        "linux": {
            "binaries": ["opera"],
            "data_path": [".config", "opera"],
            "native_messaging_path": [".config", "opera", "NativeMessagingHosts"],
        },
        "windows": {
            "data_path": ["Opera Software", "Opera Stable"],
            "registry_key": "HKCU\\Software\\Opera Software\\Opera Stable\\NativeMessagingHosts",
            "use_roaming": True,
        },
    },
}


def get_extension_ids() -> list[str]:
    """Get all Chrome extension IDs to check for installation."""
    ids = [PROD_EXTENSION_ID]
    if os.environ.get("USER_TYPE") == "ant":
        ids.append(DEV_EXTENSION_ID)
        ids.append(ANT_EXTENSION_ID)
    return ids


def get_allowed_origins() -> list[str]:
    """Get allowed_origins list for the native host manifest."""
    origins = [f"chrome-extension://{PROD_EXTENSION_ID}/"]
    if os.environ.get("USER_TYPE") == "ant":
        origins.append(f"chrome-extension://{DEV_EXTENSION_ID}/")
        origins.append(f"chrome-extension://{ANT_EXTENSION_ID}/")
    return origins


def get_all_browser_data_paths() -> list[dict]:
    """Get all browser data paths for extension detection.

    Returns list of {browser: ChromiumBrowser, path: str} dicts.
    """
    home = Path.home()
    paths: list[dict] = []

    for browser_id in BROWSER_DETECTION_ORDER:
        config = CHROMIUM_BROWSERS[browser_id]

        if sys.platform == "darwin":
            segments = config.get("macos", {}).get("data_path", [])
            if segments:
                paths.append({"browser": browser_id, "path": str(home.joinpath(*segments))})
        elif sys.platform == "linux":
            segments = config.get("linux", {}).get("data_path", [])
            if segments:
                paths.append({"browser": browser_id, "path": str(home.joinpath(*segments))})
        elif sys.platform == "win32":
            win_cfg = config.get("windows", {})
            segments = win_cfg.get("data_path", [])
            if segments:
                appdata_base = "Roaming" if win_cfg.get("use_roaming") else "Local"
                base = Path(home, "AppData", appdata_base)
                paths.append({"browser": browser_id, "path": str(base.joinpath(*segments))})

    return paths
