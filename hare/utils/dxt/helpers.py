"""
DXT plugin format helpers.

Port of: src/utils/dxt/helpers.ts + zip.ts
"""

from __future__ import annotations

from typing import Any


def validate_dxt_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate a DXT plugin manifest, return list of errors."""
    errors: list[str] = []
    if not manifest.get("name"):
        errors.append("Missing required field: name")
    if not manifest.get("version"):
        errors.append("Missing required field: version")
    if not manifest.get("description"):
        errors.append("Missing required field: description")
    return errors
