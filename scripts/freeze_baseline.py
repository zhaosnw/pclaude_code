#!/usr/bin/env python3
"""
Freeze alignment baseline for release comparison.

Port of: ALIGNMENT_EVALUATION_AND_CI_PLAN.md §6.4

Captures current alignment_data.json, coverage, stub counts, and mypy baseline
as a release snapshot. Used by release.yml to detect regressions.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT_DATA = PROJECT_ROOT / "legacy_alignment" / "alignment_data.json"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Freeze alignment baseline for release"
    )
    parser.add_argument("--tag", default=None, help="Release tag (e.g., v2.1.0)")
    parser.add_argument("--output", "-o", default="baseline.json", help="Output file")
    args = parser.parse_args()

    baseline: dict = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "tag": args.tag or "dev",
    }

    # 1. alignment_data.json snapshot
    if ALIGNMENT_DATA.exists():
        with open(ALIGNMENT_DATA, "r", encoding="utf-8") as f:
            alignment = json.load(f)
        rows = alignment.get("rows", [])
        baseline["alignment"] = {
            "total_rows": len(rows),
            "priorities": {
                "P0": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("priority") == "P0"
                ),
                "P1": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("priority") == "P1"
                ),
                "P2": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("priority") == "P2"
                ),
                "P3": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("priority") == "P3"
                ),
            },
            "statuses": {
                "done": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("status") == "done"
                ),
                "missing": sum(
                    1
                    for r in rows
                    if isinstance(r, dict) and r.get("status") == "missing"
                ),
                "stub": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("status") == "stub"
                ),
                "tiny": sum(
                    1 for r in rows if isinstance(r, dict) and r.get("status") == "tiny"
                ),
                "partial": sum(
                    1
                    for r in rows
                    if isinstance(r, dict) and r.get("status") == "partial"
                ),
            },
        }

    # 2. Test counts
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        for line in result.stderr.splitlines() + result.stdout.splitlines():
            if "collected" in line.lower():
                try:
                    count = int(line.strip().split()[-1])
                    baseline["tests"] = {"collected": count}
                except (ValueError, IndexError):
                    pass
    except Exception:
        baseline["tests"] = {"collected": "error"}

    # 3. Mypy baseline
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "hare/", "--ignore-missing-imports"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=120,
        )
        for line in reversed(result.stdout.strip().splitlines()):
            if "Found" in line and "errors" in line:
                parts = line.strip().split()
                baseline["mypy"] = {
                    "errors": int(parts[1]),
                    "files": int(parts[4]),
                    "full_line": line.strip(),
                }
                break
    except Exception:
        baseline["mypy"] = {"errors": "error"}

    # 4. Stub counts
    try:
        result = subprocess.run(
            [sys.executable, "scripts/detect_stubs.py"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
        for line in result.stdout.splitlines():
            if "raise NotImplementedError:" in line:
                baseline["stubs"] = {"nie": int(line.strip().split(":")[-1].strip())}
            elif "TODO/FIXME" in line:
                baseline["stubs"]["todo"] = int(line.strip().split(":")[-1].strip())
    except Exception:
        baseline["stubs"] = {"nie": "error", "todo": "error"}

    # 5. Coverage (from most recent xml)
    coverage_files = sorted(PROJECT_ROOT.glob("coverage*.xml"))
    if coverage_files:
        baseline["coverage_xml"] = str(coverage_files[-1].name)

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    print(f"Baseline frozen to: {output_path}")
    print(json.dumps(baseline, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
