"""Port of: src/utils/settings/managedPath.ts"""

from __future__ import annotations
import os


def get_managed_settings_drop_in_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".hare", "settings.d")
