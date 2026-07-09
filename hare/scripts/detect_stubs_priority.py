#!/usr/bin/env python3
"""Detect stubs/TODOs with exact priority mapping from alignment_data.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PY_PACKAGE_ROOT = PROJECT_ROOT / "hare" / "hare"
ALIGNMENT_DATA = PROJECT_ROOT / "legacy_alignment" / "alignment_data.json"

TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|STUB)\b", re.IGNORECASE)
NIE_PATTERN = re.compile(r"\braise\s+NotImplementedError\b")
EXCLUDE_DIRS = {"__pycache__", ".mypy_cache", ".pytest_cache", "tests", "scripts"}
THRESHOLDS = {
    "P0": {"nie": 0, "todo": 0},
    "P1": {"nie": 0, "todo": 10},
    "P2": {"nie": 20, "todo": 200},
}


def load_priority_map() -> dict[str, str]:
    data = json.loads(ALIGNMENT_DATA.read_text(encoding="utf-8"))
    priority_map: dict[str, str] = {}
    for entry in data.get("rows", []):
        if not isinstance(entry, dict):
            continue
        py_path = str(entry.get("py", ""))
        if py_path and " | " not in py_path and py_path.startswith("hare/hare/"):
            priority_map[py_path] = str(entry.get("priority", ""))
    return priority_map


def scan_file(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return len(NIE_PATTERN.findall(text)), len(TODO_PATTERN.findall(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect stubs by priority")
    parser.add_argument("--max-nie", type=int, default=None)
    parser.add_argument("--max-todo", type=int, default=None)
    parser.add_argument("--p0-fail", action="store_true")
    args = parser.parse_args()

    priority_map = load_priority_map()
    by_priority: dict[str, dict[str, int]] = {}
    unknown_files: list[str] = []
    mapped_files = set(priority_map)
    total_nie = 0
    total_todo = 0

    for file_path in sorted(PY_PACKAGE_ROOT.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in file_path.parts):
            continue
        rel_path = str(file_path.relative_to(PROJECT_ROOT))
        nie_count, todo_count = scan_file(file_path)
        if not nie_count and not todo_count:
            continue

        priority = priority_map.get(rel_path)
        if not priority:
            if rel_path in mapped_files:
                unknown_files.append(rel_path)
            continue

        stats = by_priority.setdefault(priority, {"nie": 0, "todo": 0})
        stats["nie"] += nie_count
        stats["todo"] += todo_count
        total_nie += nie_count
        total_todo += todo_count

    print(f"\n{'Priority':10s} {'NIE':>6s} {'TODO':>6s}")
    print("-" * 24)
    for priority in sorted(by_priority):
        print(
            f"{priority:10s} {by_priority[priority]['nie']:>6d} {by_priority[priority]['todo']:>6d}"
        )
    print("-" * 24)
    print(f"{'TOTAL':10s} {total_nie:>6d} {total_todo:>6d}")

    if unknown_files:
        print("\nUNKNOWN priority files with stub markers:")
        for path in unknown_files[:20]:
            print(f"  - {path}")

    exit_code = 0
    if unknown_files:
        print(
            "\nFAIL: stub-bearing files exist outside exact alignment priority mapping"
        )
        exit_code = 1

    priorities_to_check = ["P0"] if args.p0_fail else ["P0", "P1", "P2"]
    for priority in priorities_to_check:
        thresholds = THRESHOLDS[priority]
        stats = by_priority.get(priority, {"nie": 0, "todo": 0})
        if stats["nie"] > thresholds["nie"]:
            print(
                f"FAIL [{priority}]: NotImplementedError {stats['nie']} > {thresholds['nie']}"
            )
            exit_code = 1
        if stats["todo"] > thresholds["todo"]:
            print(f"FAIL [{priority}]: TODO {stats['todo']} > {thresholds['todo']}")
            exit_code = 1

    if args.max_nie is not None and total_nie > args.max_nie:
        print(f"FAIL [global]: NotImplementedError {total_nie} > {args.max_nie}")
        exit_code = 1
    if args.max_todo is not None and total_todo > args.max_todo:
        print(f"FAIL [global]: TODO {total_todo} > {args.max_todo}")
        exit_code = 1

    if exit_code == 0:
        print("\nAll stub thresholds passed.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
