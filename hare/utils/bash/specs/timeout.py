"""Port of: src/utils/bash/specs/timeout.ts"""

from __future__ import annotations

from typing import Any

TIMEOUT_SPEC: dict[str, Any] = {
    "name": "timeout",
    "description": "Run a command with a time limit",
    "args": [
        {
            "name": "duration",
            "description": "Duration to wait before timing out (e.g., 10, 5s, 2m)",
            "isOptional": False,
        },
        {
            "name": "command",
            "description": "Command to run",
            "isCommand": True,
        },
    ],
}
