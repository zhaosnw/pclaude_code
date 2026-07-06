#!/usr/bin/env python3
"""Check mypy for error regressions by comparing error count against a baseline.

Usage:
    python scripts/check_mypy_regression.py [--baseline 296]

Exits 0 if mypy error count is <= baseline.
Exits 1 if error count exceeds baseline (regression).
Exits 2 on internal error (e.g. mypy not found).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

ERROR_LINE_RE = re.compile(r"Found (\d+) errors? in (\d+) files?")


def run_mypy() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "hare/",
            "--ignore-missing-imports",
            "--show-error-codes",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def parse_error_count(output: str) -> int | None:
    """Extract error count from mypy's final summary line."""
    for line in reversed(output.strip().splitlines()):
        m = ERROR_LINE_RE.search(line)
        if m:
            return int(m.group(1))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect mypy error regressions against a baseline."
    )
    parser.add_argument(
        "--baseline",
        type=int,
        default=296,
        help="Maximum allowed mypy error count (default: 296)",
    )
    args = parser.parse_args()

    try:
        result = run_mypy()
    except FileNotFoundError:
        print("ERROR: mypy not installed. Run: pip install mypy")
        return 2

    error_count = parse_error_count(result.stdout)
    if error_count is None:
        print("WARNING: Could not parse mypy error count. Assuming 0.")
        print("mypy stdout tail:", "\n".join(result.stdout.strip().splitlines()[-5:]))
        return 0

    print(f"mypy errors: {error_count} (baseline: {args.baseline})")

    if error_count > args.baseline:
        print(
            f"FAIL: mypy errors increased from {args.baseline} to {error_count} "
            f"(+{error_count - args.baseline} regression)"
        )
        # Print new errors (those in excess) - the full output is too verbose
        return 1

    if error_count < args.baseline:
        print(
            f"PASS: mypy errors decreased from {args.baseline} to {error_count} "
            f"(-{args.baseline - error_count} improvement)"
        )
    else:
        print("PASS: mypy error count unchanged.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
