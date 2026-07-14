#!/usr/bin/env python3
"""Records which hook event fired, one marker file per event.

Registered for every session/prompt lifecycle event so a case can assert the
exact set the reference fires — the events carry no tool and produce no output,
so the marker files are the only observable.
"""

import json
import sys

payload = sys.stdin.read()
try:
    event = json.loads(payload)
except json.JSONDecodeError:
    event = {}

name = event.get("hook_event_name", "UNKNOWN")
with open(f"fired_{name}.txt", "w", encoding="utf-8") as handle:
    handle.write(f"{name}\n")

print(json.dumps({}))
