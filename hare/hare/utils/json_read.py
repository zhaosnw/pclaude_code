"""Leaf UTF-8 BOM strip — port of `src/utils/jsonRead.ts`."""

from __future__ import annotations

UTF8_BOM = "\ufeff"


def strip_bom(content: str) -> str:
    """Strip UTF-8 BOM if present (e.g. PowerShell 5.x default output)."""
    return content[1:] if content.startswith(UTF8_BOM) else content
