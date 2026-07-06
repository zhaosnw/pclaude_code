"""Parse permission rules from settings strings. Port of permissionRuleParser.ts."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedRule:
    raw: str
    tool: str | None = None
    pattern: str | None = None


def parse_permission_rule(line: str) -> ParsedRule | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = re.match(r"^(allow|deny)\s+(\S+)\s+(.+)$", line, re.I)
    if not m:
        return ParsedRule(raw=line)
    return ParsedRule(raw=line, tool=m.group(2), pattern=m.group(3))
