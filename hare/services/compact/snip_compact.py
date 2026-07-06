"""Port of: src/services/compact/snipCompact.ts

Snip compaction — lightweight content removal.
Stub — not yet implemented.
"""

from __future__ import annotations
from typing import Any


def snip_compact_if_needed(messages: list[Any]) -> dict[str, Any]:
    return {
        "messages": messages,
        "tokensFreed": 0,
        "boundaryMessage": None,
    }


def is_snip_runtime_enabled() -> bool:
    return False
