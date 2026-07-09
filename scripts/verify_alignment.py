#!/usr/bin/env python3
"""Verify alignment_data.json integrity for Phase 1 alignment gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TS_SRC = PROJECT_ROOT / "recovered-from-cli-js-map" / "src"
ALIGNMENT_FILE = PROJECT_ROOT / "legacy_alignment" / "alignment_data.json"
PY_PACKAGE_ROOT = PROJECT_ROOT / "hare"


def verify() -> int:
    if not ALIGNMENT_FILE.exists():
        print(f"ERROR: {ALIGNMENT_FILE} not found")
        return 1

    try:
        data = json.loads(ALIGNMENT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON in {ALIGNMENT_FILE}: {exc}")
        return 1

    rows = data.get("rows", [])
    if not isinstance(rows, list):
        print("ERROR: alignment_data.json rows[] missing or invalid")
        return 1

    missing_ts = 0
    missing_py_done = 0
    invalid_py_root = 0
    invalid_expected_root = 0
    status_counts: dict[str, int] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        status = str(row.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1

        ts_path = str(row.get("ts", ""))
        if ts_path and not (TS_SRC / ts_path).exists():
            missing_ts += 1
            if missing_ts <= 10:
                print(f"  MISSING TS: {ts_path}")

        py_path = str(row.get("py", ""))
        expected_py = str(row.get("expected_py", ""))

        if (
            status != "renamed?"
            and py_path
            and " | " not in py_path
            and not py_path.startswith("hare/")
        ):
            invalid_py_root += 1
            if invalid_py_root <= 10:
                print(f"  INVALID PY ROOT: {py_path}")
        if expected_py and not expected_py.startswith("hare/"):
            invalid_expected_root += 1
            if invalid_expected_root <= 10:
                print(f"  INVALID EXPECTED ROOT: {expected_py}")

        if status == "done" and py_path and " | " not in py_path:
            full_py = PROJECT_ROOT / py_path
            if not full_py.exists():
                missing_py_done += 1
                if missing_py_done <= 10:
                    print(f"  MISSING PY (done): {py_path}")

    print(f"Alignment data: {len(rows)} rows")
    print("\nStatus distribution:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status:15s}: {count:5d}")

    print("\nIssues found:")
    print(f"  TS files missing on disk:      {missing_ts}")
    print(f"  Python 'done' files missing:   {missing_py_done}")
    print(f"  Invalid py root paths:         {invalid_py_root}")
    print(f"  Invalid expected_py root path: {invalid_expected_root}")

    if missing_ts > 50:
        print("\nERROR: Too many TS files missing.")
        return 1
    if missing_py_done > 10:
        print("\nERROR: Too many files marked done but missing.")
        return 1
    if invalid_py_root or invalid_expected_root:
        print("\nERROR: alignment_data.json still contains non-canonical python paths.")
        return 1

    if not PY_PACKAGE_ROOT.exists():
        print(f"\nERROR: Python package root missing: {PY_PACKAGE_ROOT}")
        return 1

    print("\nAlignment data verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(verify())
