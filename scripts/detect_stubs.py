#!/usr/bin/env python3
"""Count stub markers, TODOs, and NotImplementedError raises across the hare codebase.

Reports counts and exits non-zero if thresholds are exceeded.

Usage:
    python scripts/detect_stubs.py
    python scripts/detect_stubs.py --max-stubs 200 --max-todos 500
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARE_PKG = PROJECT_ROOT / "hare"

STUB_PATTERN = re.compile(
    r"(raise\s+NotImplementedError|#\s*(TODO|FIXME|HACK|XXX|STUB|stub))",
    re.IGNORECASE,
)

EXCLUDE_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", "scripts"}


def count_stubs(max_stubs: int, max_todos: int) -> int:
    """Walk the hare package and count stub markers. Returns exit code."""
    files = sorted(
        f
        for f in HARE_PKG.rglob("*.py")
        if not any(ex in f.parts for ex in EXCLUDE_DIRS)
    )

    total_not_implemented = 0
    total_todos = 0
    file_counts: dict[str, dict[str, int]] = {}

    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue

        nie = text.count("raise NotImplementedError")
        td = len(re.findall(r"#\s*(TODO|FIXME|HACK|XXX|STUB)", text, re.IGNORECASE))

        if nie > 0 or td > 0:
            rel = str(f.relative_to(PROJECT_ROOT))
            file_counts[rel] = {"not_implemented": nie, "todos": td}
            total_not_implemented += nie
            total_todos += td

    # Report worst offenders
    print("\n--- Top 20 files by stub count ---")
    ranked = sorted(
        file_counts.items(),
        key=lambda x: x[1]["not_implemented"] + x[1]["todos"],
        reverse=True,
    )[:20]
    for path, counts in ranked:
        print(
            f"  {path:70s}  NIE={counts['not_implemented']:3d}  TODO={counts['todos']:3d}"
        )

    print("\n--- Totals ---")
    print(f"  raise NotImplementedError: {total_not_implemented}")
    print(f"  TODO/FIXME/HACK/XXX/STUB:  {total_todos}")
    print(f"  Files with stubs:           {len(file_counts)} / {len(files)}")

    exit_code = 0
    if total_not_implemented > max_stubs:
        print(
            f"\nERROR: NotImplementedError count ({total_not_implemented}) exceeds max ({max_stubs})"
        )
        exit_code = 1
    if total_todos > max_todos:
        print(f"\nERROR: TODO count ({total_todos}) exceeds max ({max_todos})")
        exit_code = 1

    if exit_code == 0:
        print("\nStub counts within acceptable limits.")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect stubs and TODOs")
    parser.add_argument("--max-stubs", type=int, default=200)
    parser.add_argument("--max-todos", type=int, default=500)
    args = parser.parse_args()
    return count_stubs(args.max_stubs, args.max_todos)


if __name__ == "__main__":
    sys.exit(main())
