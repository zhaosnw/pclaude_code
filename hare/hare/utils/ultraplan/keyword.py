"""
Ultraplan keyword detection.

Port of: src/utils/ultraplan/keyword.ts
"""

from __future__ import annotations

import re

ULTRAPLAN_KEYWORDS = ["ultrathink", "megathink", "ultraplan"]


def detect_ultraplan_keyword(text: str) -> str | None:
    """Detect an ultraplan keyword in user input."""
    lower = text.lower()
    for kw in ULTRAPLAN_KEYWORDS:
        if kw in lower:
            return kw
    return None


def strip_ultraplan_keyword(text: str) -> str:
    """Remove ultraplan keyword from text."""
    result = text
    for kw in ULTRAPLAN_KEYWORDS:
        result = re.sub(rf"\b{kw}\b", "", result, flags=re.IGNORECASE).strip()
    return result
