"""Port of: src/utils/bash/specs/nohup.ts"""

from __future__ import annotations

from typing import Any

NOHUP_SPEC: dict[str, Any] = {
    "name": "nohup",
    "description": "Run a command immune to hangups",
    "args": {
        "name": "command",
        "description": "Command to run with nohup",
        "isCommand": True,
    },
}
