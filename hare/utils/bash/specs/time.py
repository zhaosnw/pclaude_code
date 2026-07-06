"""Port of: src/utils/bash/specs/time.ts"""

from __future__ import annotations

from typing import Any

TIME_SPEC: dict[str, Any] = {
    "name": "time",
    "description": "Time a command",
    "args": {
        "name": "command",
        "description": "Command to time",
        "isCommand": True,
    },
}
