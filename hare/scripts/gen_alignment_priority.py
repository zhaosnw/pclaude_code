#!/usr/bin/env python3
"""
Assign and normalize alignment metadata in alignment_data.json.

Phase 1 rules:
- `rows[].py` must be repo-root relative and point into `hare/hare/...`
- `priority` is derived from the normalized python path
- `behavior_status` defaults to `unverified`
- `excluded`/`reason_if_excluded` placeholders are always present
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_DATA = PROJECT_ROOT / "alignment_data.json"

P0_PATTERNS = [
    "hare/hare/query/",
    "hare/hare/query_engine.py",
    "hare/hare/tool.py",
    "hare/hare/tools.py",
    "hare/hare/entrypoints/cli.py",
    "hare/hare/main.py",
    "hare/hare/cli/print_handler.py",
    "hare/hare/cli/structured_io.py",
    "hare/hare/cli/ndjson_safe_stringify.py",
    "hare/hare/services/mcp/",
    "hare/hare/session_setup.py",
    "hare/hare/utils/messages",
    "hare/hare/utils/errors.py",
    "hare/hare/app_types/permissions.py",
]

P1_PATTERNS = [
    "hare/hare/commands.py",
    "hare/hare/commands_impl/",
    "hare/hare/bootstrap/state.py",
    "hare/hare/cost_tracker.py",
    "hare/hare/cost_hook.py",
    "hare/hare/plugins/",
    "hare/hare/services/compact/",
    "hare/hare/query/stop_hooks.py",
    "hare/hare/query/token_budget.py",
    "hare/hare/utils/config.py",
    "hare/hare/utils/env_utils.py",
    "hare/hare/utils/settings/",
]

P2_PATTERNS = [
    "hare/hare/bridge/",
    "hare/hare/remote/",
    "hare/hare/services/analytics/",
    "hare/hare/services/voice",
    "hare/hare/services/lsp/",
    "hare/hare/tasks/",
    "hare/hare/vim/",
    "hare/hare/buddy/",
    "hare/hare/assistant/",
]

P3_PATTERNS = [
    "hare/hare/utils/native_installer/",
    "hare/hare/utils/secure_storage/",
]


def normalize_py_path(raw: str) -> str:
    """Normalize path to repo-root relative `hare/hare/...` form."""
    raw = raw.strip()
    if not raw:
        return ""
    if " | " in raw:
        return raw
    raw = raw.replace("\\", "/")
    if raw.startswith("hare/hare/"):
        return raw
    if raw.startswith("hare/"):
        return f"hare/{raw}"
    return raw


def assign_priority(py_path: str) -> str:
    for pattern in P0_PATTERNS:
        if py_path == pattern or py_path.startswith(pattern):
            return "P0"
    for pattern in P1_PATTERNS:
        if py_path == pattern or py_path.startswith(pattern):
            return "P1"
    for pattern in P2_PATTERNS:
        if py_path == pattern or py_path.startswith(pattern):
            return "P2"
    for pattern in P3_PATTERNS:
        if py_path == pattern or py_path.startswith(pattern):
            return "P3"
    return "P2"


def main() -> int:
    if not ALIGNMENT_DATA.exists():
        print(f"ERROR: {ALIGNMENT_DATA} not found", file=sys.stderr)
        return 1

    data = json.loads(ALIGNMENT_DATA.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        print("ERROR: alignment_data.json missing rows[]", file=sys.stderr)
        return 1

    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    normalized_paths = 0

    for entry in rows:
        if not isinstance(entry, dict):
            continue

        py_path = normalize_py_path(str(entry.get("py", entry.get("py_path", ""))))
        expected_py = normalize_py_path(str(entry.get("expected_py", "")))
        if py_path and py_path != entry.get("py"):
            entry["py"] = py_path
            normalized_paths += 1
        if expected_py and expected_py != entry.get("expected_py"):
            entry["expected_py"] = expected_py

        if py_path and " | " not in py_path:
            priority = assign_priority(py_path)
            entry["priority"] = priority
            counts[priority] += 1
        elif not py_path:
            entry.setdefault("priority", "P2")

        entry.setdefault("behavior_status", "unverified")
        entry.setdefault("excluded", False)
        entry.setdefault("reason_if_excluded", "")

    ALIGNMENT_DATA.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Normalized python paths: {normalized_paths}")
    for priority, count in counts.items():
        print(f"{priority}: {count}")
    print(f"Written: {ALIGNMENT_DATA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
