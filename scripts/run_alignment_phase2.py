#!/usr/bin/env python3
"""Run minimal dual-side alignment flow for Phase 2 cases."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT_ROOT = PROJECT_ROOT / "legacy_alignment"
CASES_ROOT = ALIGNMENT_ROOT / "cases"
PY_RUNNER = PROJECT_ROOT / "scripts" / "alignment_runner.py"
COMPARE = PROJECT_ROOT / "scripts" / "compare_alignment.py"
TS_RUNNER = (
    PROJECT_ROOT / "recovered-from-cli-js-map" / "alignment-harness" / "runner.ts"
)


def load_cases(priorities: set[str]) -> list[Path]:
    case_paths = []
    for case_file in sorted(CASES_ROOT.glob("**/case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        if case["priority"] in priorities:
            case_paths.append(case_file)
    return case_paths


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 2 dual-side alignment flow")
    parser.add_argument("--priority", default="P0,P1")
    parser.add_argument(
        "--report-dir", default=str(PROJECT_ROOT / "alignment-artifacts")
    )
    parser.add_argument(
        "--with-ts",
        action="store_true",
        help="Include TS oracle (default: Python-only)",
    )
    args = parser.parse_args()

    priorities = {item.strip() for item in args.priority.split(",") if item.strip()}
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    ts_jsonl = report_dir / "ts.jsonl"
    py_jsonl = report_dir / "py.jsonl"
    report_json = report_dir / "alignment-report.json"
    report_md = report_dir / "alignment-report.md"

    if args.with_ts:
        with ts_jsonl.open("w", encoding="utf-8") as ts_out:
            for case_file in load_cases(priorities):
                proc = subprocess.run(
                    ["bun", str(TS_RUNNER), "--case-file", str(case_file)],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                    check=True,
                )
                ts_out.write(proc.stdout.strip() + "\n")
    else:
        ts_jsonl.touch()
        ts_jsonl.write_text("", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(PY_RUNNER),
            "--cases-dir",
            str(CASES_ROOT),
            "--priority",
            ",".join(sorted(priorities)),
            "--out",
            str(py_jsonl),
        ],
        cwd=str(PROJECT_ROOT),
        check=True,
    )

    compare_args = [
        sys.executable,
        str(COMPARE),
        "--ts",
        str(ts_jsonl),
        "--py",
        str(py_jsonl),
        "--cases-dir",
        str(CASES_ROOT),
        "--priority",
        ",".join(sorted(priorities)),
        "--report",
        str(report_json),
        "--md",
        str(report_md),
    ]
    if not args.with_ts:
        compare_args.append("--py-only")

    compare = subprocess.run(
        compare_args,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
    )
    sys.stdout.write(compare.stdout)
    sys.stderr.write(compare.stderr)
    return compare.returncode


if __name__ == "__main__":
    sys.exit(main())
