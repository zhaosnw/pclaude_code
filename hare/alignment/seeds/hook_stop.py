#!/usr/bin/env python3
"""Stop hook that records it ran at the end of the turn."""

import json
import sys

payload = sys.stdin.read()
try:
    event = json.loads(payload)
except json.JSONDecodeError:
    event = {}

with open("hook_stop_ran.txt", "w", encoding="utf-8") as handle:
    handle.write(f"{event.get('hook_event_name', '?')}\n")

print(json.dumps({}))
