"""
Unicode sanitization for hidden-character attack mitigation.

Port of: src/utils/sanitization.ts
"""

from __future__ import annotations

import unicodedata
from typing import Any

_MAX_ITERATIONS = 10

_BAD_CATEGORIES = frozenset({"Cf", "Co", "Cn"})


def _strip_unicode_categories(s: str) -> str:
    """Strip Cf / Co / Cn categories (format controls, private use, noncharacters)."""
    return "".join(ch for ch in s if unicodedata.category(ch) not in _BAD_CATEGORIES)


def _strip_fallback_ranges(s: str) -> str:
    """Explicit ranges for environments where category stripping might miss edge cases."""
    out = []
    for ch in s:
        o = ord(ch)
        if 0x200B <= o <= 0x200F:
            continue
        if 0x202A <= o <= 0x202E:
            continue
        if 0x2066 <= o <= 0x2069:
            continue
        if o == 0xFEFF:
            continue
        if 0xE000 <= o <= 0xF8FF:
            continue
        out.append(ch)
    return "".join(out)


def partially_sanitize_unicode(prompt: str) -> str:
    """Apply NFKC normalization and strip dangerous Unicode categories."""
    current = prompt
    previous = ""
    iterations = 0

    while current != previous and iterations < _MAX_ITERATIONS:
        previous = current
        current = unicodedata.normalize("NFKC", current)
        current = _strip_unicode_categories(current)
        current = _strip_fallback_ranges(current)
        iterations += 1

    if iterations >= _MAX_ITERATIONS:
        raise RuntimeError(
            f"Unicode sanitization reached maximum iterations ({_MAX_ITERATIONS}) "
            f"for input: {prompt[:100]!r}"
        )
    return current


def recursively_sanitize_unicode(value: Any) -> Any:
    """Recursively sanitize strings in nested structures."""
    if isinstance(value, str):
        return partially_sanitize_unicode(value)
    if isinstance(value, list):
        return [recursively_sanitize_unicode(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, val in value.items():
            sk = recursively_sanitize_unicode(k)
            if not isinstance(sk, str):
                sk = str(sk)
            out[sk] = recursively_sanitize_unicode(val)
        return out
    return value
