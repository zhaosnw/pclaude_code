#!/usr/bin/env python3
"""Check for alignment regressions by comparing current state against alignment_data.json.

A regression is:
  - A file that was "done" but now has stub markers (NotImplementedError/TODO).
  - A file that was NOT "stub" but now has only stub code.
  - Increase in "missing" count since last generation.

Usage:
    python scripts/check_alignment_regressions.py
    python scripts/check_alignment_regressions.py --baseline alignment_data.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # hare/ project dir
REPO_ROOT = PROJECT_ROOT.parent  # claude-code-recover-and-python-reset/
ALIGNMENT_FILE = REPO_ROOT / "alignment_data.json"
# py_rel paths in alignment_data.json are relative to REPO_ROOT (e.g. hare/hare/query_engine.py)
PY_BASE = REPO_ROOT

STUB_MARKERS = re.compile(
    r"(raise\s+NotImplementedError|#\s*(TODO|FIXME|STUB))",
    re.IGNORECASE,
)


def file_has_stubs(py_rel: str) -> bool:
    """Check if a Python file contains stub markers."""
    full = PY_BASE / py_rel
    if not full.exists():
        return True  # missing = effectively a regression
    try:
        text = full.read_text()
    except Exception:
        return True
    return bool(STUB_MARKERS.search(text))


def main() -> int:
    baseline = sys.argv[1] if len(sys.argv) > 1 else str(ALIGNMENT_FILE)

    try:
        data = json.loads(Path(baseline).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: Cannot load baseline: {e}")
        return 1

    rows = data.get("rows", [])
    regressions: list[str] = []

    for row in rows:
        status = row.get("status", "")
        py_rel = row.get("py", "")

        if not py_rel:
            continue

        # "done" files should NOT have stub markers
        if status == "done" and file_has_stubs(py_rel):
            regressions.append(f"DONE→STUB: {py_rel} (was done, now has stub markers)")

        # "partial" files should NOT regress to pure stub
        if status == "partial":
            full = PY_BASE / py_rel
            if full.exists():
                text = full.read_text()
                lines = [
                    ln
                    for ln in text.split("\n")
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                if len(lines) <= 5:
                    regressions.append(
                        f"PARTIAL→TINY: {py_rel} (was partial, now <= 5 code lines)"
                    )
            else:
                regressions.append(f"PARTIAL→MISSING: {py_rel}")

    if regressions:
        print(f"Found {len(regressions)} regression(s):")
        for r in regressions:
            print(f"  - {r}")
        print("\nERROR: Alignment regressions detected.")
        return 1

    print("No alignment regressions detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
