"""
Heuristic secret scanning for team memory payloads.

Port of: src/services/teamMemorySync/secretScanner.ts
"""

from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
]


def scan_for_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits
