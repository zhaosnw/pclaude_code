"""Port of: src/utils/plugins/ (validator subset)"""

from __future__ import annotations
from typing import Any


def validate_plugin_manifest(manifest: dict[str, Any]) -> list[str]:
    errors = []
    if not manifest.get("name"):
        errors.append("Missing 'name'")
    if not manifest.get("version"):
        errors.append("Missing 'version'")
    return errors
