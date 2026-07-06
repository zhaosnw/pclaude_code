"""Port of: src/utils/stringUtils.ts (escapeRegExp part)"""

from __future__ import annotations
import re


def escape_regexp(s: str) -> str:
    return re.escape(s)


def capitalize(s: str) -> str:
    return s[0].upper() + s[1:] if s else ""
