#!/usr/bin/env python3
"""Audit TS↔Py size deltas using canonical alignment_data.json paths."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TS_ROOT = PROJECT_ROOT / "recovered-from-cli-js-map" / "src"
ALIGNMENT_DATA = PROJECT_ROOT / "alignment_data.json"


def count_lines(path: Path) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit TS↔Py line count deltas")
    parser.add_argument(
        "--fail-on-priority", default="", help="Comma-separated priorities to fail on"
    )
    args = parser.parse_args()

    fail_priorities = {
        item.strip() for item in args.fail_on_priority.split(",") if item.strip()
    }
    data = json.loads(ALIGNMENT_DATA.read_text(encoding="utf-8"))
    rows = data.get("rows", [])

    report_rows: list[dict[str, object]] = []
    flagged_failures = 0
    ts_total = 0
    py_total = 0

    for entry in rows:
        if not isinstance(entry, dict):
            continue
        ts_path = str(entry.get("ts", ""))
        py_path = str(entry.get("py", ""))
        if not ts_path or not py_path or " | " in py_path:
            continue

        ts_full = TS_ROOT / ts_path
        py_full = PROJECT_ROOT / py_path
        ts_lines = count_lines(ts_full)
        py_lines = count_lines(py_full)
        if ts_lines is None or py_lines is None or ts_lines == 0:
            continue

        ts_total += ts_lines
        py_total += py_lines
        ratio = py_lines / ts_lines
        flag = ""
        if ratio < 0.4:
            flag = "TOO_SMALL"
        elif ratio > 1.8:
            flag = "TOO_LARGE"

        priority = str(entry.get("priority", "P2"))
        if flag and priority in fail_priorities:
            flagged_failures += 1

        report_rows.append(
            {
                "ts_path": ts_path,
                "py_path": py_path,
                "priority": priority,
                "status": entry.get("status", ""),
                "ts_lines": ts_lines,
                "py_lines": py_lines,
                "ratio": round(ratio, 2),
                "flag": flag,
            }
        )

    report_rows.sort(
        key=lambda row: (
            row["flag"] == "",
            row["priority"],
            abs(float(row["ratio"]) - 1.0),
        ),
        reverse=True,
    )

    print("# TS↔Py Size Diff Audit")
    print(f"TS total lines: {ts_total}")
    print(f"Py total lines: {py_total}")
    if ts_total:
        print(f"Overall ratio: {py_total / ts_total:.2f}")
    print()
    print(f"{'Flag':12s} {'Priority':8s} {'Ratio':>7s} {'TS':>6s} {'Py':>6s}  Path")
    print("-" * 90)
    for row in report_rows[:50]:
        print(
            f"{str(row['flag']):12s} {str(row['priority']):8s} {row['ratio']:>7} "
            f"{row['ts_lines']:>6} {row['py_lines']:>6}  {row['ts_path']}"
        )

    audit_dir = PROJECT_ROOT / "audit"
    audit_dir.mkdir(exist_ok=True)
    csv_path = audit_dir / "sizediff.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ts_path",
                "py_path",
                "priority",
                "status",
                "ts_lines",
                "py_lines",
                "ratio",
                "flag",
            ],
        )
        writer.writeheader()
        writer.writerows(report_rows)
    print(f"\nCSV written to: {csv_path}")

    if flagged_failures:
        print(
            f"\nFAIL: {flagged_failures} flagged rows in fail-on priorities: {sorted(fail_priorities)}"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
