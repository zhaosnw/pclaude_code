"""Port of: src/utils/bash/specs/sleep.ts"""

from __future__ import annotations

from typing import Any

SLEEP_SPEC: dict[str, Any] = {
    "name": "sleep",
    "description": "Delay for a specified amount of time",
    "args": {
        "name": "duration",
        "description": "Duration to sleep (seconds or with suffix like 5s, 2m, 1h)",
        "isOptional": False,
    },
}
