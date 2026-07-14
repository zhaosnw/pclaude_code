#!/usr/bin/env python3
"""PreToolUse hook that allows Bash and records that it ran.

The marker file is the only proof the hook executed: with a scripted fixture
the model's text is identical whether or not the hook ran.
"""

import json
import sys

payload = sys.stdin.read()
try:
    event = json.loads(payload)
except json.JSONDecodeError:
    event = {}

with open("hook_allow_ran.txt", "w", encoding="utf-8") as handle:
    handle.write(f"{event.get('hook_event_name', '?')} {event.get('tool_name', '?')}\n")

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Allowed by alignment hook.",
            }
        }
    )
)
