#!/usr/bin/env python3
"""PreToolUse hook that denies Bash and records that it ran.

Reads the hook payload on stdin and writes a marker file so the case can
prove the hook actually executed (scripted fixtures make the model's text
identical whether or not the hook ran).
"""

import json
import sys

payload = sys.stdin.read()
try:
    event = json.loads(payload)
except json.JSONDecodeError:
    event = {}

with open("hook_pretool_ran.txt", "w", encoding="utf-8") as handle:
    handle.write(f"{event.get('hook_event_name', '?')} {event.get('tool_name', '?')}\n")

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Blocked by alignment hook.",
            }
        }
    )
)
