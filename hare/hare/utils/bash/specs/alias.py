"""Port of: src/utils/bash/specs/alias.ts"""

from __future__ import annotations

from typing import Any

ALIAS_SPEC: dict[str, Any] = {
    "name": "alias",
    "description": "Create or list command aliases",
    "args": {
        "name": "definition",
        "description": "Alias definition in the form name=value",
        "isOptional": True,
        "isVariadic": True,
    },
}
