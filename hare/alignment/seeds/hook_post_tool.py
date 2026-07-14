#!/usr/bin/env python3
"""PostToolUse hook that records that it ran and returns additional context."""

import json
import sys

payload = sys.stdin.read()
try:
    event = json.loads(payload)
except json.JSONDecodeError:
    event = {}

with open("hook_post_ran.txt", "w", encoding="utf-8") as handle:
    handle.write(f"{event.get('hook_event_name', '?')} {event.get('tool_name', '?')}\n")

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "post-hook context marker",
            }
        }
    )
)
